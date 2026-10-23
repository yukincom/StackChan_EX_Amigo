use http::Uri;
use serde_json::{json, Value};
use std::io::Read;
use std::net::IpAddr;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use wait_timeout::ChildExt;

pub const MINIMUM_UV: &str = "0.13.4";
pub const MAX_PROBE_BYTES: u64 = 64 * 1024;

#[derive(Debug)]
pub struct ProbeError(pub &'static str);

pub fn loopback_base_url(value: &str) -> Result<String, ProbeError> {
    let invalid = || ProbeError("ValueError");
    let uri: Uri = value.parse().map_err(|_| invalid())?;
    if !uri
        .scheme_str()
        .is_some_and(|s| s.eq_ignore_ascii_case("http"))
        || value.contains(['@', '#'])
        || uri.query().is_some()
        || !matches!(uri.path(), "" | "/")
    {
        return Err(invalid());
    }
    let authority = uri.authority().ok_or_else(invalid)?;
    let host = authority
        .host()
        .trim_start_matches('[')
        .trim_end_matches(']')
        .to_ascii_lowercase();
    if host != "localhost"
        && !host
            .parse::<IpAddr>()
            .is_ok_and(|address| address.is_loopback())
    {
        return Err(invalid());
    }
    // http::Authority::port() returns None for both absent AND invalid ports.
    // Inspect the original text so 65536 / nonnumeric cannot become port 80.
    let raw = authority.as_str();
    let suffix = if raw.starts_with('[') {
        let (_, suffix) = raw.split_once(']').ok_or_else(invalid)?;
        if suffix.is_empty() {
            None
        } else {
            Some(suffix.strip_prefix(':').ok_or_else(invalid)?)
        }
    } else {
        if host.contains(':') {
            return Err(invalid());
        }
        raw.split_once(':').map(|(_, port)| port)
    };
    let port = match suffix {
        None | Some("") => 80,
        Some(value) => {
            if !value.bytes().all(|v| v.is_ascii_digit()) {
                return Err(invalid());
            }
            let value: u16 = value.parse().map_err(|_| invalid())?;
            if value == 0 {
                80
            } else {
                value
            }
        }
    };
    let host = if host.contains(':') {
        format!("[{host}]")
    } else {
        host
    };
    Ok(format!("http://{host}:{port}"))
}

pub fn read_probe(base: &str, path: &str) -> Result<(u16, Value), ProbeError> {
    // The status API has always been plain HTTP on loopback. No TLS client or
    // root bundle is compiled in. Proxies and redirects cannot escape loopback.
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(3)))
        .http_status_as_error(false)
        .max_redirects(0)
        .proxy(None)
        .build()
        .into();
    let mut response = agent
        .get(format!("{base}{path}"))
        .call()
        .map_err(|_| ProbeError("URLError"))?;
    let status = response.status();
    if status.is_redirection() {
        return Err(ProbeError("URLError"));
    }
    let mut bytes = Vec::new();
    response
        .body_mut()
        .as_reader()
        .take(MAX_PROBE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| ProbeError("URLError"))?;
    if bytes.len() as u64 > MAX_PROBE_BYTES {
        return Err(ProbeError("RuntimeError"));
    }
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| ProbeError("JSONDecodeError"))?;
    if !value.is_object() {
        return Err(ProbeError("RuntimeError"));
    }
    Ok((status.as_u16(), value))
}

pub fn status_report(url: &str) -> (i32, Value) {
    let result = (|| {
        let base = loopback_base_url(url)?;
        let (live_status, live) = read_probe(&base, "/health/live")?;
        let (ready_status, ready) = read_probe(&base, "/health/ready")?;
        Ok::<_, ProbeError>((live_status, live, ready_status, ready))
    })();
    match result {
        Ok((live_status, live, ready_status, ready)) => {
            let good = live_status == 200 && ready_status == 200;
            (
                if good { 0 } else { 2 },
                json!({"status": if good { "ready" } else { "not_ready" }, "live": live, "ready": ready}),
            )
        }
        Err(error) => (1, json!({"status": "unreachable", "error_type": error.0})),
    }
}

pub fn version_at_least(actual: &str, minimum: &str) -> bool {
    fn parts(value: &str) -> Option<Vec<&str>> {
        value
            .split('.')
            .map(|piece| {
                if piece.is_empty() || !piece.bytes().all(|v| v.is_ascii_digit()) {
                    None
                } else {
                    let value = piece.trim_start_matches('0');
                    Some(if value.is_empty() { "0" } else { value })
                }
            })
            .collect()
    }
    let (Some(mut actual), Some(mut minimum)) = (parts(actual), parts(minimum)) else {
        return false;
    };
    let length = actual.len().max(minimum.len());
    actual.resize(length, "0");
    minimum.resize(length, "0");
    for (actual, minimum) in actual.into_iter().zip(minimum) {
        match actual
            .len()
            .cmp(&minimum.len())
            .then_with(|| actual.cmp(minimum))
        {
            std::cmp::Ordering::Greater => return true,
            std::cmp::Ordering::Less => return false,
            std::cmp::Ordering::Equal => {}
        }
    }
    true
}

pub fn require_updated_uv(executable: &std::path::Path) -> Result<(), String> {
    check_uv_with_timeout(executable, Duration::from_secs(10))
}

fn check_uv_with_timeout(executable: &std::path::Path, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    let mut child = Command::new(executable)
        .arg("--version")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "uv executable is unavailable; run python tools/bootstrap.py".to_owned())?;
    let mut stdout = child.stdout.take().ok_or("uv stdout unavailable")?;
    let (sender, receiver) = std::sync::mpsc::channel();
    // Drain while waiting: wrapper output must not fill the OS pipe. Retain
    // only a bounded prefix. A descendant may keep stdout open after its parent
    // exits; the receiver deadline also covers that case and we never join a
    // blocked reader. This CLI exits immediately when the check fails.
    std::thread::spawn(move || {
        let mut prefix = Vec::new();
        let mut buffer = [0_u8; 4096];
        let mut truncated = false;
        let result = loop {
            match stdout.read(&mut buffer) {
                Ok(0) => break Ok((prefix, truncated)),
                Ok(size) => {
                    let retained = size.min(4096 - prefix.len());
                    prefix.extend_from_slice(&buffer[..retained]);
                    truncated |= retained < size;
                }
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(error) => break Err(error),
            }
        };
        let _ = sender.send(result);
    });
    let status = child
        .wait_timeout(deadline.saturating_duration_since(Instant::now()))
        .map_err(|_| "uv version check failed")?;
    let Some(status) = status else {
        let _ = child.kill();
        let _ = child.wait();
        return Err("uv version check timed out".to_owned());
    };
    let (prefix, truncated) = receiver
        .recv_timeout(deadline.saturating_duration_since(Instant::now()))
        .map_err(|_| "uv version check timed out")?
        .map_err(|_| "uv version output failed")?;
    let version = String::from_utf8_lossy(&prefix);
    if truncated
        && version.split_whitespace().count() <= 2
        && !version.ends_with(char::is_whitespace)
    {
        return Err("uv version output is incomplete".to_owned());
    }
    let mut fields = version.split_whitespace();
    if !status.success()
        || fields.next() != Some("uv")
        || !fields
            .next()
            .is_some_and(|v| version_at_least(v, MINIMUM_UV))
    {
        return Err(format!(
            "uv>={MINIMUM_UV} is required; run python tools/bootstrap.py"
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loopback_urls_preserve_the_existing_contract() {
        for (input, expected) in [
            ("http://127.0.0.1:5050", "http://127.0.0.1:5050"),
            ("http://LOCALHOST/", "http://localhost:80"),
            ("http://[::1]:5050", "http://[::1]:5050"),
            ("http://127.12.4.9:0", "http://127.12.4.9:80"),
        ] {
            assert_eq!(loopback_base_url(input).unwrap(), expected);
        }
    }

    #[test]
    fn non_loopback_or_ambiguous_urls_are_rejected() {
        for value in [
            "https://127.0.0.1",
            "http://example.com",
            "http://127.1",
            "http://2130706433",
            "http://localhost.evil",
            "http://user:pass@127.0.0.1",
            "http://localhost/path",
            "http://localhost?key=private",
            "http://localhost#fragment",
            "http://[::2]",
            "http://localhost:65536",
            "http://localhost:bad",
            "http://localhost\r\nInjected: value",
        ] {
            assert!(loopback_base_url(value).is_err(), "{value}");
        }
    }

    #[test]
    fn uv_floor_handles_boundaries_and_unrecognized_versions() {
        for (actual, expected) in [
            ("0.10.2", false),
            ("0.13.3", false),
            ("0.13.4", true),
            ("0.13.4.0", true),
            ("0.14.0", true),
            ("1.0.0", true),
            ("0.13.4rc1", false),
            ("0..13", false),
            ("", false),
            ("999999999999999999999999", true),
            ("000.013.0004", true),
        ] {
            assert_eq!(version_at_least(actual, MINIMUM_UV), expected, "{actual}");
        }
    }

    #[cfg(unix)]
    fn uv_script(body: &str) -> std::path::PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path =
            std::env::temp_dir().join(format!("stackchan-uv-{}-{stamp}.sh", std::process::id()));
        std::fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        path
    }

    #[test]
    #[cfg(unix)]
    fn normal_and_verbose_uv_wrappers_are_drained() {
        for body in [
            "printf 'uv 0.13.4\\n'",
            "printf 'uv 0.13.4\\n'; yes output | head -c 1048576",
        ] {
            let path = uv_script(body);
            let result = check_uv_with_timeout(&path, Duration::from_secs(2));
            std::fs::remove_file(path).unwrap();
            assert!(result.is_ok(), "{result:?}");
        }
    }

    #[test]
    #[cfg(unix)]
    fn descendants_holding_stdout_do_not_escape_the_deadline() {
        let path = uv_script("printf 'uv 0.13.4\\n'; sleep 1 &\nexit 0");
        let start = Instant::now();
        let result = check_uv_with_timeout(&path, Duration::from_millis(100));
        std::fs::remove_file(path).unwrap();
        assert!(result.unwrap_err().contains("timed out"));
        assert!(start.elapsed() < Duration::from_millis(600));
    }
}
