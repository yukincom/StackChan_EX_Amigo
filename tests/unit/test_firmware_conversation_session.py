from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("source", ["conversation_session", "conversation_session_v2"])
def test_conversation_session_contract_on_host(tmp_path, source):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "conversation-session"
    subprocess.run([
        "c++", "-std=c++17", "-UNDEBUG", "-Wall", "-Wextra", "-Wno-deprecated-declarations",
        "-I", str(root / "firmware/src"),
        "-I", str(root / "firmware/.pio/libdeps/m5stack-cores3/ArduinoJson/src"),
        str(root / f"tests/firmware/{source}.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
