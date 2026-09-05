from pathlib import Path
import subprocess


def test_audio_output_rms_and_mouth_envelope_on_host(tmp_path):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "audio-level"
    subprocess.run([
        "c++", "-std=c++11", "-Wall", "-Wextra",
        "-I", str(root / "tests/firmware/stubs"),
        "-I", str(root / "firmware/src"),
        str(root / "tests/firmware/audio_level.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
