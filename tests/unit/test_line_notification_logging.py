"""Keep LINE receive events visible in the existing server log, without real I/O."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import requests


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "server/services/scheduler_service.py"


def polling_function(get):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "poll_render")
    notify = Mock()
    namespace = {
        "requests": SimpleNamespace(get=get, exceptions=requests.exceptions),
        "config": SimpleNamespace(RENDER_URL="https://fixture.invalid"),
        "mask_names": lambda text: text.replace("実名", "[家族１]"),
        "process_notification": notify,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["poll_render"], notify


def test_line_event_uses_existing_log_and_preserves_delivery(capsys):
    message = "実名からの通知\n次の行"
    response = Mock()
    response.json.return_value = {"notification": {"user_id": "private-user-id", "message": message}}
    get = Mock(return_value=response)
    poll, notify = polling_function(get)

    poll()

    get.assert_called_once_with("https://fixture.invalid/poll", timeout=60)
    response.raise_for_status.assert_called_once_with()
    notify.assert_called_once_with("private-user-id", message)
    output = capsys.readouterr().out
    assert "[LINE] 新着メッセージ:" in output
    assert "[家族１]" in output and "実名" not in output
    assert "private-user-id" not in output
    assert len(output.splitlines()) == 1


def test_empty_poll_does_not_add_log_noise(capsys):
    response = Mock()
    response.json.return_value = {"notification": None}
    poll, notify = polling_function(Mock(return_value=response))
    poll()
    notify.assert_not_called()
    assert capsys.readouterr().out == ""


def test_long_poll_timeout_stays_quiet(capsys):
    poll, notify = polling_function(Mock(side_effect=requests.exceptions.Timeout()))
    poll()
    notify.assert_not_called()
    assert capsys.readouterr().out == ""


def test_existing_poll_errors_are_still_logged(capsys):
    poll, notify = polling_function(Mock(side_effect=requests.exceptions.ConnectionError("fixture error")))
    poll()
    notify.assert_not_called()
    assert "[ERROR] poll_render: fixture error" in capsys.readouterr().out
