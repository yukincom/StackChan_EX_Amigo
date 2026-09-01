from __future__ import annotations

import app as app_module


def test_create_app_has_no_runtime_side_effects():
    flask_app = app_module.create_app({"TESTING": True})

    assert app_module._RUNTIME_STARTED is False
    assert flask_app.config["TESTING"] is True
    assert flask_app.view_functions["health_check"] is app_module.health_check
    assert flask_app.view_functions["vision_upload"] is app_module.vision_upload

    response = flask_app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["runtime_started"] is False
    assert flask_app.test_client().get("/health/live").status_code == 200
    not_ready = flask_app.test_client().get("/health/ready")
    assert not_ready.status_code == 503
    assert not_ready.get_json()["checks"]["runtime"] is False


def test_readiness_requires_all_runtime_boundaries(monkeypatch):
    class AliveThread:
        def is_alive(self):
            return True

    class ReadyJournal:
        def readiness(self):
            return True, "ready"

    monkeypatch.setattr(app_module, "_RUNTIME_STARTED", True)
    monkeypatch.setattr(app_module, "_VISION_WORKER_STARTED", True)
    monkeypatch.setattr(app_module, "_VISION_WORKER_THREAD", AliveThread())
    monkeypatch.setattr(app_module, "voice_delivery_ready", lambda: True)
    monkeypatch.setattr(app_module, "get_operation_journal", lambda: ReadyJournal())
    monkeypatch.setattr(app_module.config, "LOAD_ERRORS", [])

    response = app_module.create_app({"TESTING": True}).test_client().get("/health/ready")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"


def test_runtime_services_are_idempotent(monkeypatch):
    calls: list[str] = []
    app_module._RUNTIME_STARTED = False

    monkeypatch.setattr(app_module, "run_if_needed", lambda: calls.append("batch"))
    monkeypatch.setattr(app_module, "_init_last_message_id", lambda: calls.append("discord"))
    monkeypatch.setattr(app_module, "check_notification_config", lambda: True)
    monkeypatch.setattr(app_module, "setup_scheduler", lambda enabled: calls.append(f"scheduler:{enabled}"))
    monkeypatch.setattr(app_module, "preload_mlx_model", lambda: calls.append("llm"))
    monkeypatch.setattr(app_module, "preload_vml_model", lambda: calls.append("vlm"))
    monkeypatch.setattr(app_module, "warmup_stack_startup_message", lambda: calls.append("voice"))
    monkeypatch.setattr(app_module, "stop_scheduler", lambda: calls.append("stop"))
    monkeypatch.setattr(app_module, "_recover_operation_journal", lambda: calls.append("recover"))
    monkeypatch.setattr(app_module, "_ensure_vision_worker", lambda: calls.append("vision"))
    monkeypatch.setattr(app_module, "ensure_voice_delivery_worker", lambda: calls.append("delivery"))
    monkeypatch.setattr(app_module.config, "DISCORD_BOT_TOKEN", "configured")

    assert app_module.start_runtime_services() is True
    assert app_module.start_runtime_services() is False
    assert calls == [
        "recover", "vision", "delivery", "batch", "discord", "scheduler:True", "llm", "vlm", "voice",
    ]

    assert app_module.stop_runtime_services() is True
    assert app_module.stop_runtime_services() is False
    assert calls[-1] == "stop"
