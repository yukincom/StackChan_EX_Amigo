"""Lazy-load and idle-unload lifecycle tests without real models or network."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"


def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_app_startup_does_not_preload_vlm_or_mlx():
    app_source = (SERVER / "app.py").read_text(encoding="utf-8")
    assert "preload_mlx_model()" not in app_source
    assert "preload_vml_model()" not in app_source


def test_vlm_loads_on_demand_and_unloads_after_idle_timeout(monkeypatch, tmp_path):
    vlm = _load_module("public_vlm_lifecycle", "services/vlm_service.py")
    assert vlm.VLM_IDLE_UNLOAD_SECONDS == 300.0

    image = tmp_path / "sample.jpg"
    image.write_bytes(b"fake-jpg")

    fake_model = object()
    fake_processor = object()
    fake_config = {"model_type": "qwen3_vl"}
    clear_cache = MagicMock()

    monkeypatch.setattr(vlm, "_hf_model_cache_dir", lambda: None)
    monkeypatch.setitem(sys.modules, "mlx", types.SimpleNamespace(core=types.SimpleNamespace(clear_cache=clear_cache)))
    monkeypatch.setitem(sys.modules, "mlx.core", sys.modules["mlx"].core)
    monkeypatch.setitem(
        sys.modules,
        "mlx_vlm",
        types.SimpleNamespace(
            load=MagicMock(return_value=(fake_model, fake_processor)),
            generate=MagicMock(return_value="テスト画像"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "mlx_vlm.utils",
        types.SimpleNamespace(
            get_model_path=lambda _: "/fixture/vlm",
            load_config=lambda _: fake_config,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "mlx_vlm.prompt_utils",
        types.SimpleNamespace(apply_chat_template=lambda *a, **kw: "prompt"),
    )

    scheduled = []

    class FakeTimer:
        def __init__(self, interval, fn):
            self.interval = interval
            self.fn = fn
            self.daemon = False
            self.name = ""
            self.cancelled = False
            self.started = False
            scheduled.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(vlm.threading, "Timer", FakeTimer)

    result = vlm.analyze_image(str(image))
    assert result == "テスト画像"
    assert vlm.is_vlm_ready() is True
    assert len(scheduled) == 1
    assert scheduled[0].interval == 300.0
    assert scheduled[0].started is True

    result2 = vlm.analyze_image(str(image))
    assert result2 == "テスト画像"
    assert scheduled[0].cancelled is True
    assert len(scheduled) == 2

    scheduled[-1].fn()
    assert vlm.is_vlm_ready() is False
    assert vlm._model is None
    assert vlm._processor is None
    assert vlm._model_config is None
    clear_cache.assert_called_once()


def test_llm_mlx_unloads_after_three_minutes_idle(monkeypatch):
    cfg_stub = types.SimpleNamespace(
        AI_PROVIDER="mlx",
        AI_CHAT_MODEL="test/mlx-chat",
        AI_SUMMARY_PROVIDER="mlx",
        AI_SUMMARY_MODEL="test/mlx-chat",
        AI_SEARCH_PROVIDER="gemini",
        AI_SEARCH_MODEL="gemini-2.5-flash",
        AI_MAX_OUTPUT_TOKENS=100,
        AI_CHAT_TEMPERATURE=0.8,
        AI_SUMMARY_TEMPERATURE=0.3,
        AI_SEARCH_TEMPERATURE=0.3,
        OPENAI_BASE_URL="https://api.openai.com/v1",
        THINKING_STRIP_PATTERNS=[],
    )
    monkeypatch.setitem(sys.modules, "config", types.SimpleNamespace(config=cfg_stub))
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(
            try_to_load_from_cache=lambda *a, **kw: "/cached/config.json",
            _CACHED_NO_EXIST=object(),
        ),
    )

    client = _load_module("public_llm_lifecycle", "llm_client.py")
    assert client.MLX_IDLE_UNLOAD_SECONDS == 180.0

    fake_processor = types.SimpleNamespace(
        apply_chat_template=lambda *a, **kw: "formatted"
    )
    fake_model = object()
    clear_cache = MagicMock()
    monkeypatch.setitem(sys.modules, "mlx.core", types.SimpleNamespace(clear_cache=clear_cache))
    monkeypatch.setitem(
        sys.modules,
        "mlx_lm",
        types.SimpleNamespace(
            load=MagicMock(return_value=(fake_model, fake_processor)),
            generate=MagicMock(return_value="こんにちは"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "mlx_lm.sample_utils",
        types.SimpleNamespace(make_sampler=lambda temp: None),
    )

    scheduled = []

    class FakeTimer:
        def __init__(self, interval, fn):
            self.interval = interval
            self.fn = fn
            self.daemon = False
            self.name = ""
            self.cancelled = False
            scheduled.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(client.threading, "Timer", FakeTimer)

    reply = client._call_mlx_text("おはよう", "test/mlx-chat", 0.8)
    assert reply == "こんにちは"
    assert client._mlx_model is fake_model
    assert len(scheduled) == 1
    assert scheduled[0].interval == 180.0

    scheduled[0].fn()
    assert client._mlx_model is None
    assert client._mlx_processor is None
    assert client._mlx_loaded_model_name is None
    clear_cache.assert_called_once()


def test_kokoro_lazy_import_and_mode_load_unload(monkeypatch):
    monkeypatch.delitem(sys.modules, "kokoro", raising=False)
    monkeypatch.setitem(sys.modules, "numpy", MagicMock())
    monkeypatch.setitem(sys.modules, "soundfile", MagicMock())
    monkeypatch.setitem(
        sys.modules,
        "voice_server.env_loader",
        types.SimpleNamespace(load_voice_environment=lambda: None),
    )

    tts_kokoro = _load_module("public_tts_kokoro", "voice_server/tts_kokoro.py")
    assert "kokoro" not in sys.modules
    assert tts_kokoro._kokoro_pipeline is None

    fake_pipeline = object()
    fake_kpipeline = MagicMock(return_value=fake_pipeline)
    monkeypatch.setitem(sys.modules, "kokoro", types.SimpleNamespace(KPipeline=fake_kpipeline))

    assert tts_kokoro.preload_kokoro() is True
    assert tts_kokoro._kokoro_pipeline is fake_pipeline
    fake_kpipeline.assert_called_once_with(lang_code="a")

    assert tts_kokoro.unload_kokoro() is True
    assert tts_kokoro._kokoro_pipeline is None
