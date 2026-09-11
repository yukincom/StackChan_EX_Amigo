import subprocess
import tempfile
import os

import re
from config import config
from services import conversation_forecast

WHISPER_CLI   = config.WHISPER_CLI
WHISPER_MODEL = config.WHISPER_MODEL


def _vad_arguments():
    """Return the generic Silero gate used before Whisper transcription."""
    vad_model = (config.WHISPER_VAD_MODEL or "").strip()
    if not vad_model:
        return []
    return [
        "--vad",
        "--vad-model", vad_model,
        "--vad-threshold", str(config.WHISPER_VAD_THRESHOLD),
        "--vad-min-speech-duration-ms", str(config.WHISPER_VAD_MIN_SPEECH_MS),
        "--vad-min-silence-duration-ms", str(config.WHISPER_VAD_MIN_SILENCE_MS),
        "--vad-speech-pad-ms", str(config.WHISPER_VAD_SPEECH_PAD_MS),
    ]

class SpeechService:
    """whisper.cpp ラッパー"""

    def transcribe(self, audio_content: bytes, language_code: str = "ja-JP", use_english_model: bool = False):
        """音声データをテキストに変換"""
        tmp_16k = None
        try:
            lang = language_code.split("-")[0]
            bytes_len = len(audio_content) if audio_content else 0

            # WAVを一時保存（M5Stackが16kHzで録音するのでそのまま使う）
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:#
#            import datetime #デバッグ
#            debug_dir = "/tmp/stackchan_debug"#デバッグ
#            os.makedirs(debug_dir, exist_ok=True)#デバッグ
#            ts = datetime.datetime.now().strftime("%H%M%S")#デバッグ
#            fname = f"{debug_dir}/{ts}.wav"#デバッグ
#            with open(fname, "wb") as f:#デバッグ

                f.write(audio_content)
                tmp_16k = f.name

            en_model = (config.WHISPER_MODEL_EN or "").strip()
            ja_model = (config.WHISPER_MODEL or WHISPER_MODEL or "").strip()

            if use_english_model and en_model:
                model = en_model
                lang = "en"
                prompt = "User talking to StackChan in English."
                stt_profile = "EN"
            else:
                model = ja_model
                prompt = conversation_forecast.decorate_stt_prompt("日本語の日常会話です。")
                stt_profile = "JA"
                if use_english_model and not en_model:
                    print(
                        "[SPEECH] use_english_model=True だが WHISPER_MODEL_EN が空のため JA にフォールバック"
                    )

            print(
                f"[SPEECH] start profile={stt_profile} "
                f"use_english_model={use_english_model} "
                f"lang={lang} "
                f"language_code={language_code!r} "
                f"bytes={bytes_len} "
                f"model={model!r}"
            )

            command = [
                WHISPER_CLI,
                "-m", model,
                "-f", tmp_16k,
                "-l", lang,
                "--prompt", prompt,
                "--no-speech-thold", str(config.WHISPER_NO_SPEECH_THOLD),
            ]
            vad_arguments = _vad_arguments()
            if vad_arguments:
                command.extend(vad_arguments)
            else:
                print("[SPEECH] Silero VAD disabled: WHISPER_VAD_MODEL is empty")

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            if result.returncode != 0:
                print(f"[SPEECH] whisper.cpp stderr:\n{result.stderr}")

            output = result.stdout.strip()
            lines = [line.strip() for line in output.splitlines() if line.strip()]

            transcript = ""
            for line in lines:
                if line.startswith("[") and "-->" in line:
                    parts = line.split("]", 1)
                    if len(parts) > 1:
                        text = parts[1].strip()
                        # specialトークンを除去
                        text = re.sub(r'\[_[A-Z_0-9]+_\]', '', text).strip()
                        if text:
                            transcript += text + " "
            transcript = transcript.strip()

            if not transcript:
                print(f"[SPEECH] No speech detected profile={stt_profile} lang={lang}")
                return None

            print(f"[SPEECH] ok profile={stt_profile} lang={lang} text={transcript!r}")
            return transcript

        except subprocess.TimeoutExpired:
            print("[SPEECH] タイムアウト (60秒超)")
            return None
        except subprocess.CalledProcessError as e:
            print(f"[SPEECH] 実行エラー: {e.stderr[:200]}")
            return None
        except Exception as e:
            print(f"[SPEECH] 例外: {e}")
            return None
        finally:
            # 必ず一時ファイルを削除
            for f in [tmp_16k]:#
                if f and os.path.exists(f):#
                    os.unlink(f)#
#                pass #デバッグ

# グローバルインスタンス
speech_service = SpeechService()
