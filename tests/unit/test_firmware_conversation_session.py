from pathlib import Path
import subprocess


def test_conversation_session_contract_on_host(tmp_path):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "conversation-session"
    subprocess.run([
        "c++", "-std=c++17", "-Wall", "-Wextra", "-Wno-deprecated-declarations",
        "-I", str(root / "firmware/src"),
        "-I", str(root / "firmware/.pio/libdeps/m5stack-cores3/ArduinoJson/src"),
        str(root / "tests/firmware/conversation_session.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
