# services/voice_service.py
"""音声生成サービスモジュール"""
from pathlib import Path
import threading
import time
from urllib.parse import quote, urlparse

import requests
from config import config


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
    
def push_to_m5stack(voice_url: str, fire_and_forget: bool = True) -> bool:
    """TTS生成済みのURLをM5Stackに送りつけて再生させる。"""

    def _send() -> bool:
        if fire_and_forget:
            time.sleep(1.5)
        try:
            m5stack_url = f"http://{config.M5STACK_IP}:{config.M5STACK_PORT}/play"
            playable_url = build_push_audio_url(voice_url)
            response = requests.post(
                m5stack_url,
                json={"voice_url": playable_url},
                timeout=30,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"[PUSH] エラー: {e}")
            return False

    print(f"[PUSH] M5Stack再生指示: {build_push_audio_url(voice_url)}")

    if fire_and_forget:
        threading.Thread(target=_send, daemon=True).start()
        return True

    return _send()


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
