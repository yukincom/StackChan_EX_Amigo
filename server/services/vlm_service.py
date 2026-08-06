# services/vlm_service.py
"""
VLM（画像言語モデル）サービス
使用モデル: env VLM_MODEL（既定 lmstudio-community/Qwen3-VL-8B-Instruct-MLX-4bit）
app.py 起動時に preload_vml_model() を呼んでウォームアップ。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

VLM_MODEL = os.getenv(
    "VLM_MODEL",
    "lmstudio-community/Qwen3-VL-8B-Instruct-MLX-4bit",
)

_model = None
_processor = None
_model_config = None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()
_loading = False
_last_error: str | None = None


def _strip_thinking(text: str) -> str:
    """Qwen3系モデルの<think>...</think>を除去"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def _extract_text(output) -> str | None:
    """
    mlx_vlm.generate() の戻り値差異を吸収して文字列化する。
    """
    if output is None:
        return None

    if isinstance(output, str):
        s = output.strip()
        return s or None

    for attr in ("text", "output_text", "generated_text", "response"):
        if hasattr(output, attr):
            v = getattr(output, attr)
            if isinstance(v, str):
                s = v.strip()
                return s or None

    if isinstance(output, dict):
        for k in ("text", "output_text", "generated_text", "response"):
            v = output.get(k)
            if isinstance(v, str):
                s = v.strip()
                return s or None

    s = _strip_thinking(str(output))
    return s or None


def _hf_model_cache_dir() -> Path | None:
    """HuggingFace hub キャッシュにモデルがあるか（あればネット再取得を避ける）。"""
    home = Path.home() / ".cache" / "huggingface" / "hub"
    # org/name → models--org--name
    safe = "models--" + VLM_MODEL.replace("/", "--")
    path = home / safe
    if path.is_dir():
        return path
    return None


def preload_vml_model() -> None:
    """
    app.py 起動時にバックグラウンドスレッドでモデルをロード。
    Flask の起動をブロックしない。
    """

    def _load():
        global _loading
        _loading = True
        try:
            ok = _ensure_loaded()
            logger.info("[VLM] Preload finished ok=%s model=%s", ok, VLM_MODEL)
        finally:
            _loading = False

    t = threading.Thread(target=_load, daemon=True, name="vlm-preload")
    t.start()
    logger.info("[VLM] Preload started in background: %s", VLM_MODEL)


def is_vlm_ready() -> bool:
    return _model is not None and _processor is not None


def vlm_status() -> dict:
    return {
        "ready": is_vlm_ready(),
        "loading": _loading,
        "model": VLM_MODEL,
        "last_error": _last_error,
        "cache_dir": str(_hf_model_cache_dir() or ""),
    }


def _ensure_loaded() -> bool:
    """モデルをロード（スレッドセーフ）。成功後は config もキャッシュ。"""
    global _model, _processor, _model_config, _last_error
    if _model is not None and _processor is not None and _model_config is not None:
        return True

    with _load_lock:
        if _model is not None and _processor is not None and _model_config is not None:
            return True
        try:
            # 既にキャッシュがあるときは hub への再問い合わせを抑止（固まり防止）
            if _hf_model_cache_dir() is not None:
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

            from mlx_vlm import load
            from mlx_vlm.utils import load_config

            t0 = time.monotonic()
            logger.info("[VLM] Loading %s ...", VLM_MODEL)
            _model, _processor = load(VLM_MODEL)
            _model_config = load_config(VLM_MODEL)
            elapsed = time.monotonic() - t0
            _last_error = None
            logger.info("[VLM] ✅ Model ready in %.1fs", elapsed)
            return True
        except ImportError:
            _last_error = "mlx_vlm not installed"
            logger.warning("[VLM] mlx_vlm not installed → pip install mlx-vlm")
            return False
        except Exception as e:
            _last_error = str(e)
            logger.error("[VLM] Load failed: %s", e, exc_info=True)
            # 失敗時は offline 強制を戻さない（次回オンラインで再試行できるようにする）
            return False


def analyze_image(image_path: str, user_context: str = "") -> str | None:
    """
    画像を VLM で解析して日本語説明を返す。
    戻り値: 説明文字列 or None（失敗時）
    """
    path = Path(image_path)
    if not path.is_file():
        logger.error("[VLM] image missing: %s", image_path)
        return None

    if not _ensure_loaded():
        logger.error("[VLM] model not ready (last_error=%s)", _last_error)
        return None

    # 推論は1本に直列化（並行 generate でハング・メモリ枯渇しやすい）
    with _infer_lock:
        try:
            from mlx_vlm import generate
            from mlx_vlm.prompt_utils import apply_chat_template

            if user_context:
                prompt = (
                    f"ユーザーが「{user_context}」と言いながらこの画像を見せてくれました。"
                    "何が写っているか日本語で50字以内で答えてください。/no_think"
                )
            else:
                prompt = (
                    "この画像に何が写っていますか？"
                    "日本語で50字以内で答えてください。/no_think"
                )

            formatted = apply_chat_template(
                _processor, _model_config, prompt, num_images=1
            )

            t0 = time.monotonic()
            logger.info(
                "[VLM] generate start image=%s bytes=%s context=%r",
                path.name,
                path.stat().st_size,
                (user_context or "")[:40],
            )
            output = generate(
                _model,
                _processor,
                image=str(path),
                prompt=formatted,
                verbose=False,
                max_tokens=100,
                temp=0.3,
            )
            elapsed = time.monotonic() - t0

            result = _extract_text(output)
            if result and len(result) > 80:
                result = result[:80]

            logger.info(
                "[VLM] generate done in %.1fs type=%s text=%s",
                elapsed,
                type(output).__name__,
                (result or "")[:60],
            )
            return result

        except Exception as e:
            logger.error(
                "[VLM] Analysis error: %s (model=%s image=%s)",
                e,
                VLM_MODEL,
                image_path,
                exc_info=True,
            )
            return None
