from pathlib import Path
import subprocess


def test_production_camera_and_audio_queues_on_host(tmp_path):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "webapi-actions"
    subprocess.run([
        "c++", "-std=c++17", "-Wall", "-Wextra", "-Wno-unused-parameter",
        "-Wno-deprecated-declarations",
        "-I", str(root / "tests/firmware/webapi_stubs"),
        "-I", str(root / "firmware/src"),
        "-I", str(root / "firmware/lib/m5stack-avatar/src"),
        "-I", str(root / "firmware/.pio/libdeps/m5stack-cores3/ArduinoJson/src"),
        str(root / "tests/firmware/webapi_actions.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
