# services/voice_service.py
"""音声生成サービスモジュール"""
import hashlib
import json
from pathlib import Path
import queue
import threading
import time
from urllib.parse import quote, urlparse

import requests
from config import config
from observability import emit_trace, trace_context
from operation_journal import JournalError, get_operation_journal


_VOICE_DELIVERY_QUEUE: queue.Queue[str] = queue.Queue(maxsize=100)
_VOICE_DELIVERY_LOCK = threading.Lock()
_VOICE_DELIVERY_QUEUED: set[str] = set()
_VOICE_DELIVERY_WORKER_STARTED = False
_VOICE_DELIVERY_WORKER: threading.Thread | None = None


def _server_base_url() -> str:
    parsed = urlparse(config.VOICE_SERVER_URL)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = config.SERVER_PORT
    return f"{scheme}://{host}:{port}"


def build_push_audio_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    if parsed.path.startswith("/voice/cache_") or parsed.path.endswith(".mp3"):
        return source_url
    return f"{_server_base_url()}/audio/proxy.mp3?src={quote(source_url, safe='')}"


def _voice_file_path_from_source_url(source_url: str) -> Path | None:
    parsed = urlparse(source_url)
    path = parsed.path or ""
    if not path.startswith("/voice/"):
        return None

    voice_id = path.rsplit("/", 1)[-1]
    if not voice_id or voice_id.startswith("cache_"):
        return None

    return Path(config.VOICE_STORAGE_DIR).expanduser() / f"{voice_id}.wav"


def generate_voice(text, **kwargs):
    """voice_server.py の /generate を呼ぶ。

    Args:
        text: 音声生成するテキスト
        **kwargs: speaker_idなど（省略時はvoice_server側のデフォルト使用）

    Returns:
        dict: {
            "voice_id": str,
            "source_url": str,
            "size": int,
            "sha256": str,
            "settings": dict
        } または 失敗時はNone
    """
    try:
        payload = {"text": text}
        # speaker_idが指定された場合のみ追加
        if "speaker_id" in kwargs:
            payload["speaker_id"] = kwargs["speaker_id"]

        response = requests.post(
            f"{config.VOICE_SERVER_URL}/generate",
            json=payload,
            timeout=config.VOICE_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        result = response.json()
        if not result.get("success"):
            print(f"❌ voice_server generation failed: {result.get('error')}")
            return None

        voice_id = result["voice_id"]
        download_path = result.get("download_path", f"/voice/{voice_id}")
        source_url = f"{config.VOICE_SERVER_URL}{download_path}"

        return {
            "voice_id": voice_id,
            "source_url": source_url,
            "size": result["size"],
            "sha256": result["sha256"],
            "settings": result["settings"],
        }
    except requests.exceptions.Timeout:
        print(f"❌ timeout: voice_server({config.VOICE_SERVER_URL})")
        return None
    except requests.exceptions.ConnectionError:
        print(f"❌ connection error: voice_server({config.VOICE_SERVER_URL})")
        return None
    except Exception as e:
        print(f"❌ generate_voice error: {e}")
        return None


def generate_voice_mixed(text, speaker_id=None, voice=None):
    """日英混合音声生成（/generate_mixed を呼ぶ）
    
    日本語はVOICEVOX、英語はKokoro(af_sarah)で生成・結合。
    
    Args:
        text:       読み上げるテキスト（日英混在OK）
        speaker_id: VOICEVOXの話者ID（省略時はサーバーデフォルト）
        voice:      Kokoroの声（省略時はaf_sarah）
    
    Returns:
        generate_voice() と同じ形式の dict または None
    """
    try:
        payload = {"text": text}
        if speaker_id is not None:
            payload["speaker_id"] = speaker_id
        if voice is not None:
            payload["voice"] = voice

        response = requests.post(
            f"{config.VOICE_SERVER_URL}/generate_mixed",
            json=payload,
            timeout=config.VOICE_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        result = response.json()
        if not result.get("success"):
            print(f"❌ generate_mixed failed: {result.get('error')}")
            return None

        voice_id      = result["voice_id"]
        download_path = result.get("download_path", f"/voice/{voice_id}")
        source_url    = f"{config.VOICE_SERVER_URL}{download_path}"

        return {
            "voice_id":   voice_id,
            "source_url": source_url,
            "size":       result["size"],
            "sha256":     result["sha256"],
            "settings":   result["settings"],
        }

    except requests.exceptions.Timeout:
        print(f"❌ timeout: generate_mixed")
        return None
    except requests.exceptions.ConnectionError:
        print(f"❌ connection error: generate_mixed")
        return None
    except Exception as e:
        print(f"❌ generate_voice_mixed error: {e}")
        return None
    
def _enqueue_persisted_voice_deliveries() -> None:
    journal = get_operation_journal()
    for record in journal.pending("voice_push"):
        op_id = record["op_id"]
        with _VOICE_DELIVERY_LOCK:
            if op_id in _VOICE_DELIVERY_QUEUED:
                continue
            try:
                _VOICE_DELIVERY_QUEUE.put_nowait(op_id)
            except queue.Full:
                return
            _VOICE_DELIVERY_QUEUED.add(op_id)


def _deliver_voice_operation(op_id: str) -> bool:
    journal = get_operation_journal()
    try:
        request_data = json.loads(journal.load_payload(op_id, "request.json"))
        if not isinstance(request_data, dict):
            raise JournalError("private voice request is invalid")
        voice_url = str(request_data["voice_url"])
        delay_ms = int(request_data.get("delay_ms", 0))
        if not voice_url or not 0 <= delay_ms <= 30_000:
            raise JournalError("private voice request is invalid")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError, JournalError):
        journal.transition(op_id, "failed", owner="worker", stage="error")
        return False

    journal.transition(op_id, "dispatching", owner="worker", stage="dispatch")
    try:
        if delay_ms:
            time.sleep(delay_ms / 1000)
        with trace_context(op_id):
            response = requests.post(
                f"http://{config.M5STACK_IP}:{config.M5STACK_PORT}/play",
                json={"voice_url": build_push_audio_url(voice_url)},
                timeout=30,
            )
            response.raise_for_status()
        journal.transition(op_id, "acked", owner="worker", stage="delivered")
        journal.transition(op_id, "succeeded", owner="worker", stage="complete")
        journal.discard_payloads(op_id)
        return True
    except Exception as exc:
        try:
            latest = journal.latest().get(op_id)
            if latest and latest["state"] == "dispatching":
                journal.transition(op_id, "uncertain", owner="worker", stage="error")
        except JournalError:
            pass
        emit_trace("voice", "push", "error", trace_id=op_id, error_type=type(exc).__name__)
        print(f"[PUSH] delivery failed op={op_id[:8]}")
        return False


def _voice_delivery_worker_loop() -> None:
    while True:
        op_id = _VOICE_DELIVERY_QUEUE.get()
        try:
            _deliver_voice_operation(op_id)
        except Exception as exc:
            emit_trace("voice", "push", "error", trace_id=op_id, error_type=type(exc).__name__)
        finally:
            with _VOICE_DELIVERY_LOCK:
                _VOICE_DELIVERY_QUEUED.discard(op_id)
            _VOICE_DELIVERY_QUEUE.task_done()
            try:
                _enqueue_persisted_voice_deliveries()
            except JournalError:
                pass


def ensure_voice_delivery_worker() -> None:
    global _VOICE_DELIVERY_WORKER_STARTED, _VOICE_DELIVERY_WORKER
    with _VOICE_DELIVERY_LOCK:
        if (
            _VOICE_DELIVERY_WORKER_STARTED
            and _VOICE_DELIVERY_WORKER is not None
            and _VOICE_DELIVERY_WORKER.is_alive()
        ):
            return
        worker = threading.Thread(
            target=_voice_delivery_worker_loop,
            name="voice-delivery-worker",
            daemon=True,
        )
        worker.start()
        _VOICE_DELIVERY_WORKER = worker
        _VOICE_DELIVERY_WORKER_STARTED = True
    _enqueue_persisted_voice_deliveries()


def voice_delivery_ready() -> bool:
    return bool(
        _VOICE_DELIVERY_WORKER_STARTED
        and _VOICE_DELIVERY_WORKER is not None
        and _VOICE_DELIVERY_WORKER.is_alive()
    )


def _voice_operation_id(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
        raise ValueError("idempotency key is invalid")
    return hashlib.sha256(f"voice_push\0{idempotency_key}".encode("utf-8")).hexdigest()[:32]


def push_to_m5stack(
    voice_url: str,
    fire_and_forget: bool = True,
    *,
    idempotency_key: str | None = None,
) -> bool:
    """Persist a playback request before asynchronous or synchronous delivery."""
    try:
        private_request = json.dumps(
            # Async dispatch must not impose an unconditional startup wait.
            # Keep delay_ms=0 in new records; the reader still accepts legacy
            # persisted delays without rewriting already-accepted operations.
            # 復旧用: 固定1.5秒待ちが必要なら次行を戻し、delay_ms=0の行と置き換える。
            # {"voice_url": voice_url, "delay_ms": 1500 if fire_and_forget else 0},
            {"voice_url": voice_url, "delay_ms": 0},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        journal = get_operation_journal()
        if idempotency_key is None:
            operation = journal.create(
                "voice_push",
                owner="worker",
                payloads={"request.json": private_request},
            )
            created = True
        else:
            operation, created = journal.create_once(
                "voice_push",
                _voice_operation_id(idempotency_key),
                owner="worker",
                payloads={"request.json": private_request},
            )
    except (JournalError, OSError, TypeError, ValueError):
        return False

    op_id = operation["op_id"]
    if not created:
        if operation["state"] in {"failed", "rejected"}:
            return False
        if fire_and_forget:
            ensure_voice_delivery_worker()
            _enqueue_persisted_voice_deliveries()
            return True
        if operation["state"] in {"accepted", "retry_wait"}:
            return _deliver_voice_operation(op_id)
        return operation["state"] in {"acked", "succeeded"}

    print(f"[PUSH] queued op={op_id[:8]}")
    if not fire_and_forget:
        return _deliver_voice_operation(op_id)

    ensure_voice_delivery_worker()
    _enqueue_persisted_voice_deliveries()
    return True


def delete_generated_voice_later(source_url: str, delay_seconds: int = 90) -> None:
    """生成済み音声を後で削除する。push用に少し待ってから消す。"""

    def _worker():
        time.sleep(delay_seconds)
        path = _voice_file_path_from_source_url(source_url)
        if path is None:
            return
        try:
            if path.exists():
                path.unlink()
                print(f"[VOICE] deleted warmup audio: {path}")
        except Exception as e:
            print(f"[VOICE] delete error: {e}")

    threading.Thread(target=_worker, daemon=True).start()


def warmup_stack_startup_message() -> None:
    """サーバー起動時の音声系 warm-up。Stack が不在でも失敗扱いにしない。"""

    def _worker():
        try:
            text = "サーバーが起動したよ！"
            voice_result = generate_voice_mixed(text, speaker_id=config.VOICEVOX_SPEAKER_ID)
            if not voice_result or not voice_result.get("source_url"):
                print("[WARMUP] voice generation skipped")
                return

            source_url = voice_result["source_url"]
            push_to_m5stack(source_url)
            delete_generated_voice_later(source_url)
            print("[WARMUP] startup voice prepared")
        except Exception as e:
            print(f"[WARMUP] startup voice error: {e}")

    threading.Thread(target=_worker, daemon=True).start()

def cleanup_old_voice_files(max_age_seconds=3600, keep_latest=True):
    """古い音声ファイルを削除する"""
    try:
        response = requests.post(
            f"{config.VOICE_SERVER_URL}/cleanup",
            json={"max_age_seconds": max_age_seconds, "keep_latest": keep_latest},
            timeout=config.VOICE_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return int(response.json().get("deleted", 0))
    except Exception as e:
        print(f"⚠️ cleanup error: {e}")
        return 0
