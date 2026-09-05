from __future__ import annotations

import pytest

from services import scheduler_service


class _Scheduler:
    def __init__(self):
        self.jobs = []
        self.started = False

    def add_job(self, function, trigger, **kwargs):
        self.jobs.append((function, trigger, kwargs))

    def start(self):
        self.started = True


def _configure(monkeypatch):
    fake = _Scheduler()
    monkeypatch.setattr(scheduler_service, "scheduler", fake)
    monkeypatch.setattr(scheduler_service, "scheduled_cleanup", lambda: None)
    monkeypatch.setattr(scheduler_service, "update_weather", lambda *_args: None)
    monkeypatch.setattr(scheduler_service, "has_line_users", lambda: True)
    monkeypatch.setattr(scheduler_service, "has_discord_users", lambda: False)
    monkeypatch.setattr(scheduler_service.config, "ANNOUNCEMENTS", [])
    monkeypatch.setattr(scheduler_service.config, "POLL_INTERVAL", 60)
    monkeypatch.setattr(scheduler_service.config, "RENDER_URL", "")
    monkeypatch.setattr(scheduler_service, "line_queue_configured", lambda: False)
    return fake


def test_cloudflare_queue_is_optional_when_render_is_unset(monkeypatch):
    fake = _configure(monkeypatch)
    worker_calls = []
    monkeypatch.setattr(scheduler_service, "line_queue_configured", lambda: True)
    monkeypatch.setattr(
        scheduler_service,
        "ensure_line_notification_worker",
        lambda: worker_calls.append("started"),
    )
    monkeypatch.setattr(scheduler_service.config, "RENDER_URL", "  ")

    scheduler_service.setup_scheduler(notification_enabled=True)

    line_jobs = [job for job in fake.jobs if job[0] in {
        scheduler_service.poll_line_queue,
        scheduler_service.poll_render,
    }]
    assert worker_calls == ["started"]
    assert line_jobs == [
        (scheduler_service.poll_line_queue, "interval", {"seconds": 60}),
    ]
    assert fake.started is True


@pytest.mark.parametrize("queue_configured", [False, True])
def test_render_is_preferred_and_keeps_sixty_second_interval(monkeypatch, queue_configured):
    fake = _configure(monkeypatch)
    monkeypatch.setattr(scheduler_service, "line_queue_configured", lambda: queue_configured)
    monkeypatch.setattr(scheduler_service.config, "RENDER_URL", "https://render.example")
    worker_calls = []
    monkeypatch.setattr(
        scheduler_service,
        "ensure_line_notification_worker",
        lambda: worker_calls.append("started"),
    )

    scheduler_service.setup_scheduler(notification_enabled=True)

    line_jobs = [job for job in fake.jobs if job[0] in {
        scheduler_service.poll_line_queue,
        scheduler_service.poll_render,
    }]
    assert line_jobs == [
        (scheduler_service.poll_render, "interval", {"seconds": 60}),
    ]
    assert worker_calls == []
    assert scheduler_service.line_delivery_configured() is True


def test_render_does_not_require_queue_validation(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(scheduler_service.config, "RENDER_URL", "https://render.example")

    def unexpected_queue_check():
        raise AssertionError("Render must not depend on optional Queue configuration")

    monkeypatch.setattr(scheduler_service, "line_queue_configured", unexpected_queue_check)

    assert scheduler_service.line_delivery_configured() is True
    scheduler_service.setup_scheduler(notification_enabled=True)


def test_no_line_job_when_both_relays_are_unconfigured(monkeypatch):
    fake = _configure(monkeypatch)

    scheduler_service.setup_scheduler(notification_enabled=True)

    assert not any(job[0] in {
        scheduler_service.poll_line_queue,
        scheduler_service.poll_render,
    } for job in fake.jobs)
    assert scheduler_service.line_delivery_configured() is False
