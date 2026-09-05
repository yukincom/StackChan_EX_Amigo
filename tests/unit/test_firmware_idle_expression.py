from pathlib import Path
import subprocess


def test_idle_expression_policy_on_host(tmp_path):
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "idle-expression"
    subprocess.run([
        "c++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
        "-I", str(root / "firmware/src"),
        "-I", str(root / "firmware/lib/m5stack-avatar/src"),
        str(root / "tests/firmware/idle_expression_policy.cpp"), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
