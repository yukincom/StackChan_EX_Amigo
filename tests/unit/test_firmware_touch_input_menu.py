from pathlib import Path
import subprocess


def test_touch_gesture_and_input_menu_on_host(tmp_path):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "touch-input-menu"
    subprocess.run([
        "c++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
        "-I", str(root / "firmware/src"),
        str(root / "tests/firmware/touch_input_menu.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
