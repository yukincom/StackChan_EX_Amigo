from pathlib import Path
import subprocess


def test_avatar_speech_text_ownership_and_concurrent_frames(tmp_path):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "avatar-speech-text"
    subprocess.run([
        "c++", "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pthread",
        "-I", str(root / "firmware/lib/m5stack-avatar/src"),
        str(root / "tests/firmware/avatar_speech_text.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True,
                   timeout=30)
