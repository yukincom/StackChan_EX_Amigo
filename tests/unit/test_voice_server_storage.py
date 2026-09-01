from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def test_voice_server_creates_explicit_storage_directory(tmp_path, monkeypatch):
    """Exercise the standalone voice app without installing the optional models."""
    imported_names = (
        "voice_server.tts_voicevox",
        "voice_server.tts_kokoro",
        "voice_server.cache_manager",
    )
    previous_modules = {name: sys.modules.get(name) for name in imported_names}
    fake_numpy = types.ModuleType("numpy")
    fake_soundfile = types.ModuleType("soundfile")
    fake_kokoro = types.ModuleType("kokoro")
    fake_kokoro.KPipeline = object
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)
    monkeypatch.setitem(sys.modules, "kokoro", fake_kokoro)

    source = Path(__file__).resolve().parents[2] / "server" / "voice_server.py"
    spec = importlib.util.spec_from_file_location("stackchan_voice_app_test", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)

        storage = tmp_path / "nested" / "voice"
        monkeypatch.setattr(module, "VOICE_STORAGE_DIR", str(storage))
        module._ensure_voice_storage_dir()

        assert storage.is_dir()
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
