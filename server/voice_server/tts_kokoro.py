# voice_server/tts_kokoro.py
"""Kokoro TTS ロジック（英語・日英分割）"""
from __future__ import annotations
import gc
import io
import os
import re
import sys
import threading
import numpy as np
import soundfile as sf

from voice_server.env_loader import load_voice_environment


load_voice_environment()

KOKORO_VOICE_ENGLISH = os.getenv("KOKORO_VOICE_ENGLISH", "af_sarah")
KOKORO_SAMPLE_RATE = 24000

_kokoro_pipeline = None
_kokoro_lock = threading.Lock()


def _get_kokoro():
    """Kokoroパイプラインを取得（初回のみ遅延import＋初期化）"""
    global _kokoro_pipeline
    with _kokoro_lock:
        if _kokoro_pipeline is None:
            print("[KOKORO] パイプライン初期化中...")
            from kokoro import KPipeline
            _kokoro_pipeline = KPipeline(lang_code='a')  # 'a' = American English
            print("[KOKORO] 初期化完了")
        return _kokoro_pipeline


def preload_kokoro() -> bool:
    """英語モード有効化時にKokoroパイプラインを事前ロードする。"""
    return _get_kokoro() is not None


def unload_kokoro() -> bool:
    """日本語モード復帰時にKokoroパイプラインを解放する。"""
    global _kokoro_pipeline
    with _kokoro_lock:
        was_loaded = _kokoro_pipeline is not None
        _kokoro_pipeline = None
    if was_loaded:
        gc.collect()
        torch = sys.modules.get("torch")
        if torch is not None:
            try:
                if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                    torch.mps.empty_cache()
            except Exception:
                pass
        print("[KOKORO] パイプライン解放完了")
    return was_loaded


def kokoro_tts(text: str, voice: str | None = None) -> bytes | None:
    """Kokoroで英語テキストをWAVバイト列に変換"""
    if voice is None:
        voice = KOKORO_VOICE_ENGLISH
    pipeline = _get_kokoro()
    chunks = []
    for _, _, audio in pipeline(text, voice=voice):
        chunks.append(audio)
    if not chunks:
        return None
    audio_data = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, audio_data, KOKORO_SAMPLE_RATE, format='WAV', subtype='PCM_16')
    return buf.getvalue()


def split_text_by_lang(text: str) -> list[tuple[str, str]]:
    """テキストを日本語/英語セグメントに分割

    例: 「Let's go!って言ってみて！」
      → [('en', "Let's go!"), ('ja', 'って言ってみて！')]
    """
    pattern = r"([A-Za-z]+(?:[''\-][A-Za-z]+)+[A-Za-z0-9\s'\-,!?.]*|[A-Za-z]+\s+[A-Za-z0-9\s'\-,!?.]{2,}|[A-Za-z]{4,})"
    segments = []
    last_end = 0

    for match in re.finditer(pattern, text):
        if match.start() > last_end:
            ja_part = text[last_end:match.start()].strip()
            if ja_part:
                segments.append(('ja', ja_part))
        en_part = match.group().strip()
        if en_part:
            segments.append(('en', en_part))
        last_end = match.end()

    if last_end < len(text):
        remaining = text[last_end:].strip()
        if remaining:
            segments.append(('ja', remaining))

    return segments if segments else [('ja', text)]


def concat_wav_bytes(wav_list: list[bytes]) -> bytes:
    """複数のWAVバイト列を結合（サンプルレート違いも吸収）"""
    if len(wav_list) == 1:
        return wav_list[0]

    arrays = []
    base_sr = None
    for wav_bytes in wav_list:
        buf = io.BytesIO(wav_bytes)
        data, sr = sf.read(buf, dtype='float32')
        if base_sr is None:
            base_sr = sr
        elif sr != base_sr:
            new_len = int(len(data) * base_sr / sr)
            data = np.interp(
                np.linspace(0, len(data), new_len),
                np.arange(len(data)),
                data,
            )
        arrays.append(data)

    combined = np.concatenate(arrays)
    out_buf = io.BytesIO()
    sf.write(out_buf, combined, base_sr, format='WAV', subtype='PCM_16')
    return out_buf.getvalue()
