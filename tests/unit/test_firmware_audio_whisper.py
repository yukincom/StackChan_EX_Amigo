from pathlib import Path
import subprocess


def test_actual_recorder_cancellation_and_cleanup_on_host(tmp_path):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "audio-whisper"
    subprocess.run([
        "c++", "-std=c++17", "-Wall", "-Wextra", "-Wno-sign-compare",
        "-I", str(root / "tests/firmware/recorder_stubs"),
        "-I", str(root / "firmware/src"),
        str(root / "tests/firmware/audio_whisper.cpp"),
        str(root / "firmware/src/driver/AudioWhisper.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
