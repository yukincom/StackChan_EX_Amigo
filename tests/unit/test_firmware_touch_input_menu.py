from pathlib import Path
import os
import subprocess

import pytest


def test_touch_gesture_and_input_menu_on_host(tmp_path):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "touch-input-menu"
    subprocess.run([
        "c++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
        "-I", str(root / "firmware/src"),
        str(root / "tests/firmware/touch_input_menu.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)


def test_input_menu_view_uses_real_m5gfx_center_datum(tmp_path):
    root = Path(__file__).resolve().parents[2]
    configured = os.environ.get("M5GFX_INCLUDE_DIR")
    candidates = ([Path(configured)] if configured else sorted(
        (root / "firmware/.pio/libdeps").glob("*/M5GFX/src")))
    if not candidates and not configured:
        pytest.skip("M5GFX headers unavailable; run a firmware build first")
    for index, include in enumerate(candidates):
        assert (include / "lgfx/v1/misc/enum.hpp").is_file()
        binary = tmp_path / f"input-mode-menu-view-{index}"
        subprocess.run([
            "c++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
            "-I", str(root / "tests/firmware/menu_view_stubs"),
            "-I", str(root / "firmware/src"), "-I", str(include),
            str(root / "tests/firmware/input_mode_menu_view.cpp"),
            "-o", str(binary),
        ], check=True, capture_output=True, text=True)
        subprocess.run([str(binary)], check=True, capture_output=True, text=True)
