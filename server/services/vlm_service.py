# services/vlm_service.py
"""
VLM（画像言語モデル）サービス
使用モデル: lmstudio-community/Qwen3-VL-8B-Instruct-MLX-4bit
app.py 起動時に preload_vmlx_model() を呼んでウォームアップ。
"""
import os
import re
import threading
import logging

logger = logging.getLogger(__name__)

VLM_MODEL = os.getenv(
    "VLM_MODEL",
    "lmstudio-community/Qwen3-VL-8B-Instruct-MLX-4bit"
)

_model = None
_processor = None
_load_lock = threading.Lock()
_loading = False


def _strip_thinking(text: str) -> str:
    """Qwen3系モデルの<think>...</think>を除去"""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()

def _extract_text(output) -> str | None:
    """
    mlx_vlm.generate() の戻り値差異を吸収して文字列化する。
    想定:
      - str
      - GenerationResult(text=...)
      - dict
      - その他（str()フォールバック）
    """
    if output is None:
        return None

    if isinstance(output, str):
        s = output.strip()
        return s or None

    # オブジェクト属性候補
    for attr in ("text", "output_text", "generated_text", "response"):
        if hasattr(output, attr):
            v = getattr(output, attr)
            if isinstance(v, str):
                s = v.strip()
                return s or None

    # dict候補
    if isinstance(output, dict):
        for k in ("text", "output_text", "generated_text", "response"):
            v = output.get(k)
            if isinstance(v, str):
                s = v.strip()
                return s or None

    # 最終フォールバック
    s = _strip_thinking(str(output))
    return s or None


def preload_vml_model() -> None:
    """
    app.py 起動時にバックグラウンドスレッドでモデルをロード。
    Flask の起動をブロックしない。
    """

    def _load():
        global _loading
        _loading = True
        _ensure_loaded()
        _loading = False

    t = threading.Thread(target=_load, daemon=True, name="vlm-preload")
    t.start()
    logger.info("[VLM] Preload started in background: %s", VLM_MODEL)


def _ensure_loaded() -> bool:
    """モデルをロード（スレッドセーフ）"""
    global _model, _processor
    if _model is not None:
        return True

    with _load_lock:
        if _model is not None:
            return True
        try:
            from mlx_vlm import load

            logger.info("[VLM] Loading %s ...", VLM_MODEL)
            _model, _processor = load(VLM_MODEL)
            logger.info("[VLM] ✅ Model ready")
            return True
        except ImportError:
            logger.warning("[VLM] mlx_vlm not installed → pip install mlx-vlm")
            return False
        except Exception as e:
            logger.error("[VLM] Load failed: %s", e, exc_info=True)
            return False

def analyze_image(image_path: str, user_context: str = "") -> str | None:
    """
    画像を VLM で解析して日本語説明を返す。
    戻り値: 説明文字列 or None（失敗時）
    """
    if not _ensure_loaded():
        return None

    try:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config

        config = load_config(VLM_MODEL)

        if user_context:
            prompt = (
                f"ユーザーが「{user_context}」と言いながらこの画像を見せてくれました。"
                "何が写っているか日本語で50字以内で答えてください。/no_think"
            )
        else:
            prompt = (
                "この画像に何が写っていますか？"
                "この画像に何が写っていますか？日本語で50字以内で答えてください。/no_think"
            )

        formatted = apply_chat_template(_processor, config, prompt, num_images=1)

        output = generate(
            _model,
            _processor,
            image=image_path,
            prompt=formatted,
            verbose=False,
            max_tokens=100,
            temp=0.3,
        )

        result = _extract_text(output)
        
        if result and len(result) > 80:
            result = result[:80]        
        
        logger.info(
            "[VLM] ResultType=%s Result=%s...",
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
