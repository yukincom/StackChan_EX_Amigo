from __future__ import annotations

import json
from pathlib import Path

from tools.stackchan_lab.config_schema import (
    plan_migrations,
    validate_all,
    validate_json_documents,
    validate_sd_templates,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_repository_config_contract_has_no_blocking_error():
    report = validate_all(REPO_ROOT)
    assert report["status"] == "ok"
    assert report["counts"]["error"] == 0
    assert {issue["code"] for issue in report["issues"]} == {
        "SD_SERVO_LIMIT_MISSING",
        "SD_URL_PLACEHOLDER",
    }


def test_sd_secret_log_true_is_blocking_without_exposing_secret(tmp_path):
    basic = tmp_path / "Copy-to-SD" / "yaml" / "SC_BasicConfig.yaml"
    ex = tmp_path / "Copy-to-SD" / "app" / "AiStackChanEx" / "SC_ExConfig.yaml"
    basic.parent.mkdir(parents=True)
    ex.parent.mkdir(parents=True)
    sentinel = "DO_NOT_LEAK_wifi_password"
    basic.write_text(f"secret_config_show: true\nwifi_password: {sentinel}\n", encoding="utf-8")
    ex.write_text("llm:\n  base_url: http://127.0.0.1:5050\n", encoding="utf-8")

    issues = validate_sd_templates(tmp_path)
    encoded = json.dumps([issue.as_dict() for issue in issues])
    assert any(issue.code == "SD_SECRET_LOG_ENABLED" and issue.severity == "error" for issue in issues)
    assert sentinel not in encoded


def test_member_children_drift_requires_manual_mapping(tmp_path):
    documents = {
        "announcements.json": [],
        "member.json": {"children": [{"name": ["fixture"]}], "family": [], "friends": []},
        "reading_map.json": {},
        "voice_cache_catalog.json": {"pc_cache": [], "stack_sd": []},
    }
    for name, value in documents.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")

    issues = validate_json_documents(tmp_path)
    drift = [issue for issue in issues if issue.code == "MEMBER_CHILDREN_DRIFT"]
    assert len(drift) == 1
    assert drift[0].migration == "manual"


def test_migration_plan_is_read_only_and_counts_legacy_day_flags(tmp_path):
    announcements = [
        {"hour": 1, "minute": 2, "message": "fixture"},
        {
            "hour": 2, "minute": 3, "message": "fixture",
            "weekday_only": False, "holiday_only": False,
        },
        {
            "hour": 3, "minute": 4, "message": "fixture",
            "weekday_only": False, "holiday_only": False,
            "_explicit_long_vacation": True,
        },
    ]
    (tmp_path / "announcements.json").write_text(json.dumps(announcements), encoding="utf-8")
    (tmp_path / "member.json").write_text(json.dumps({"children": []}), encoding="utf-8")
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.iterdir()}

    plan = plan_migrations(tmp_path)

    after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.iterdir()}
    assert plan == {
        "schema_version": 1,
        "mode": "read_only",
        "safe_changes": 2,
        "manual_changes": 1,
        "apply_performed": False,
    }
    assert after == before
