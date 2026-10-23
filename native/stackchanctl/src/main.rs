use clap::{Args, Parser, Subcommand};
use serde_json::json;
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

#[derive(Parser)]
#[command(
    name = "stackchanctl",
    version,
    about = "Local StackChan preflight, status and service launch"
)]
struct Cli {
    #[command(subcommand)]
    command: Action,
}

#[derive(Subcommand)]
enum Action {
    Preflight(Locations),
    Status {
        #[arg(long, default_value = "http://127.0.0.1:5050")]
        url: String,
    },
    RunServer {
        #[command(flatten)]
        locations: Locations,
        #[arg(long)]
        uv: Option<PathBuf>,
        #[arg(long)]
        dry_run: bool,
    },
}

#[derive(Args)]
struct Locations {
    #[arg(long, default_value_os_t = default_repo())]
    repo: PathBuf,
    #[arg(long, default_value_os_t = home().join("env"))]
    env_dir: PathBuf,
}

fn home() -> PathBuf {
    env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_default()
}

fn default_repo() -> PathBuf {
    if let Some(value) = env::var_os("STACKCHAN_REPO_ROOT") {
        return PathBuf::from(value);
    }
    if let Ok(executable) = env::current_exe() {
        if let Some(root) = executable
            .ancestors()
            .find(|p| p.join("server/app.py").is_file() && p.join("pyproject.toml").is_file())
        {
            return root.to_owned();
        }
    }
    env::current_dir().unwrap_or_default()
}

fn absolute(path: &Path) -> Result<PathBuf, String> {
    let expanded = if path == Path::new("~") {
        home()
    } else if let Ok(rest) = path.strip_prefix("~/") {
        home().join(rest)
    } else {
        path.to_owned()
    };
    let value = if expanded.is_absolute() {
        expanded
    } else {
        env::current_dir()
            .map_err(|e| e.to_string())?
            .join(expanded)
    };
    Ok(value.canonicalize().unwrap_or(value))
}

fn repository(path: &Path) -> Result<PathBuf, String> {
    let root = absolute(path)?;
    if !root.join("server/app.py").is_file() || !root.join("pyproject.toml").is_file() {
        return Err("repository layout is invalid".to_owned());
    }
    Ok(root)
}

fn executable(path: &Path) -> bool {
    let Ok(metadata) = path.metadata() else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn on_path(name: &str) -> Option<PathBuf> {
    env::split_paths(&env::var_os("PATH")?)
        .map(|dir| {
            dir.join(if cfg!(windows) {
                format!("{name}.exe")
            } else {
                name.to_owned()
            })
        })
        .find(|p| executable(p))
}

fn print_json(value: &serde_json::Value) -> Result<(), String> {
    println!(
        "{}",
        serde_json::to_string_pretty(value).map_err(|e| e.to_string())?
    );
    Ok(())
}

fn run(cli: Cli) -> Result<i32, String> {
    match cli.command {
        Action::Status { url } => {
            let (code, report) = stackchanctl::status_report(&url);
            print_json(&report)?;
            Ok(code)
        }
        Action::Preflight(locations) => {
            let repo = repository(&locations.repo)?;
            let env_dir = absolute(&locations.env_dir)?;
            let python = env::var_os("STACKCHAN_PYTHON")
                .map(PathBuf::from)
                .or_else(|| {
                    let local = repo.join(if cfg!(windows) {
                        ".venv/Scripts/python.exe"
                    } else {
                        ".venv/bin/python"
                    });
                    if executable(&local) {
                        Some(local)
                    } else {
                        on_path("python3").or_else(|| on_path("python"))
                    }
                })
                .ok_or("Python is required for the existing offline doctor")?;
            let script = "import json,sys; from pathlib import Path; from tools.stackchan_lab.doctor import doctor_summary,run_doctor; r=doctor_summary(run_doctor(Path(sys.argv[1]),Path(sys.argv[2]))); print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)); raise SystemExit(1 if r['status']=='error' else 0)";
            let status = Command::new(python)
                .args(["-B", "-c", script])
                .arg(&repo)
                .arg(env_dir)
                .current_dir(repo)
                .status()
                .map_err(|e| e.to_string())?;
            Ok(status.code().unwrap_or(1))
        }
        Action::RunServer {
            locations,
            uv,
            dry_run,
        } => {
            let repo = repository(&locations.repo)?;
            let env_dir = absolute(&locations.env_dir)?;
            let bundled = repo.join(if cfg!(windows) {
                ".bootstrap-venv/Scripts/uv.exe"
            } else {
                ".bootstrap-venv/bin/uv"
            });
            let uv = match uv {
                Some(path) => absolute(&path)?,
                None if executable(&bundled) => bundled,
                None => on_path("uv")
                    .ok_or("uv executable is unavailable; run python tools/bootstrap.py")?,
            };
            if !executable(&uv) {
                return Err(
                    "uv executable is unavailable; run python tools/bootstrap.py".to_owned(),
                );
            }
            stackchanctl::require_updated_uv(&uv)?;
            let env_ready = env_dir.is_dir() && env_dir.join(".env").is_file();
            let args = ["run", "--frozen", "--no-sync", "python", "server/app.py"];
            if dry_run {
                let mut command = vec![uv
                    .file_name()
                    .ok_or("uv filename unavailable")?
                    .to_string_lossy()
                    .into_owned()];
                command.extend(args.iter().map(|s| s.to_string()));
                print_json(
                    &json!({"status":"dry_run", "repo_valid":true, "env_ready":env_ready, "command":command}),
                )?;
                return Ok(if env_ready { 0 } else { 2 });
            }
            if !env_ready {
                return Err("env directory or .env is unavailable".to_owned());
            }
            let mut command = Command::new(uv);
            command
                .args(args)
                .current_dir(repo)
                .env("STACKCHAN_ENV_DIR", env_dir);
            #[cfg(unix)]
            {
                use std::os::unix::process::CommandExt;
                Err(command.exec().to_string())
            }
            #[cfg(not(unix))]
            {
                Ok(command
                    .status()
                    .map_err(|e| e.to_string())?
                    .code()
                    .unwrap_or(1))
            }
        }
    }
}

fn main() -> ExitCode {
    match run(Cli::parse()) {
        Ok(code) => ExitCode::from(u8::try_from(code).unwrap_or(1)),
        Err(error) => {
            eprintln!("stackchanctl: {error}");
            ExitCode::FAILURE
        }
    }
}
