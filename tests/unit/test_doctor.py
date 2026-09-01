from __future__ import annotations

import json

from tools.stackchan_lab.doctor import doctor_summary, read_env_keys, run_doctor


def _minimal_repo(root):
    (root / "server" / "json_example").mkdir(parents=True)
    (root / "server" / "app.py").write_text("# fixture\n", encoding="utf-8")
    (root / "firmware").mkdir()
    (root / "firmware" / "platformio.ini").write_text("[platformio]\n", encoding="utf-8")
    (root / ".env.example").write_text("SERVER_PORT=5050\n", encoding="utf-8")
    (root / "server" / "json_example" / "member.json").write_text("{}\n", encoding="utf-8")


def test_doctor_never_emits_secret_values(tmp_path):
    repo = tmp_path / "repo"
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    _minimal_repo(repo)
    secret = "super-private-token-value"
    (env_dir / ".env").write_text(
        f"ADMIN_TOKEN={secret}\nSERVER_PORT=5050\nAI_CHAT_TEMPERATURE=0.8\n",
        encoding="utf-8",
    )

    summary = doctor_summary(run_doctor(repo, env_dir))
    rendered = json.dumps(summary, ensure_ascii=False)
    assert secret not in rendered
    assert summary["counts"]["ok"] > 0


def test_env_reader_detects_duplicate_keys(tmp_path):
    path = tmp_path / ".env"
    path.write_text("A=1\nB=2\nA=3\n", encoding="utf-8")
    values, duplicates = read_env_keys(path)
    assert values["A"] == "3"
    assert duplicates == ["A"]
