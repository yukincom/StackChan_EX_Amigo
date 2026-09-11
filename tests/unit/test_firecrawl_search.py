"""Provider and settings contracts; no credentials, live models or network."""
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock

from dotenv import dotenv_values
from flask import Flask
import pytest
import requests


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def adapter(monkeypatch):
    module = load("isolated_firecrawl", SERVER / "services/firecrawl_search.py")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-only")
    return module


def response(payload, status=200):
    result = MagicMock(status_code=status)
    result.__enter__.return_value = result
    result.json.return_value = payload
    return result


def test_search_request_and_sources(adapter, monkeypatch):
    post = MagicMock(return_value=response({"success": True, "data": {"web": [
        {"title": "天気", "url": "https://example.test/weather", "description": "晴れ"}
    ]}}))
    monkeypatch.setattr(adapter.requests, "post", post)
    result = adapter.search_context("今日の天気")
    assert "晴れ" in result and "https://example.test/weather" in result
    args, kwargs = post.call_args
    assert args == ("https://api.firecrawl.dev/v2/search",)
    assert kwargs["json"] == {"query": "今日の天気", "sources": ["web"]}
    assert kwargs["headers"] == {"Authorization": "Bearer fc-test-only"}
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status", [401, 402, 429, 500, 302])
def test_http_errors_do_not_echo_body_or_key(adapter, monkeypatch, status):
    post = MagicMock(return_value=response({"error": "fc-test-only private"}, status))
    monkeypatch.setattr(adapter.requests, "post", post)
    with pytest.raises(RuntimeError) as error:
        adapter.search_context("query")
    assert str(status) in str(error.value)
    assert "fc-test-only" not in str(error.value)
    assert post.call_count == 1


@pytest.mark.parametrize("payload", [None, {}, {"success": False}, {"success": True, "data": []}])
def test_malformed_result(adapter, monkeypatch, payload):
    monkeypatch.setattr(adapter.requests, "post", MagicMock(return_value=response(payload)))
    with pytest.raises(RuntimeError):
        adapter.search_context("query")


def test_empty_results(adapter, monkeypatch):
    monkeypatch.setattr(adapter.requests, "post", MagicMock(return_value=response(
        {"success": True, "data": {"web": []}})))
    assert adapter.search_context("query") == "検索結果はありません。"


def test_no_key_no_network(adapter, monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY")
    post = MagicMock()
    monkeypatch.setattr(adapter.requests, "post", post)
    with pytest.raises(ValueError):
        adapter.search_context("query")
    post.assert_not_called()


def test_timeout_redacted(adapter, monkeypatch):
    monkeypatch.setattr(adapter.requests, "post", MagicMock(side_effect=requests.Timeout("fc-test-only")))
    with pytest.raises(RuntimeError) as error:
        adapter.search_context("query")
    assert "fc-test-only" not in str(error.value)


@pytest.fixture
def modules(monkeypatch, tmp_path, adapter):
    cfg = types.SimpleNamespace(
        AI_PROVIDER="mlx", AI_CHAT_MODEL="chat-test", AI_SEARCH_PROVIDER="gemini",
        AI_SEARCH_MODEL="gemini-test", AI_SEARCH_TEMPERATURE=0.3,
        ENV_DIR=str(tmp_path), JSON_DIR=str(tmp_path), ADMIN_TOKEN="",
    )
    fake = types.ModuleType("config")
    fake.config = cfg
    monkeypatch.setitem(sys.modules, "config", fake)
    monkeypatch.syspath_prepend(str(SERVER))
    monkeypatch.setitem(sys.modules, "services.firecrawl_search", adapter)
    catalog = types.ModuleType("services.voice_cache_catalog")
    for name in ("generate_mp3_file", "get_pc_cache_audio_path", "get_stack_sd_audio_path", "load_catalog", "save_catalog"):
        setattr(catalog, name, MagicMock())
    monkeypatch.setitem(sys.modules, "services.voice_cache_catalog", catalog)
    client = load("isolated_search_client", SERVER / "llm_client.py")
    admin = load("isolated_search_admin", SERVER / "admin_routes.py")
    admin._ENV_DIR = tmp_path
    return cfg, client, admin


def test_provider_dispatch(modules, adapter, monkeypatch):
    cfg, client, _ = modules
    gemini = MagicMock(return_value="gemini answer")
    chat = MagicMock(return_value="chat answer")
    search = MagicMock(return_value="source data")
    monkeypatch.setattr(client, "_call_gemini", gemini)
    monkeypatch.setattr(client, "call", chat)
    monkeypatch.setattr(adapter, "search_context", search)
    assert client.call_search("persona and history", query="masked query") == "gemini answer"
    search.assert_not_called()
    assert gemini.call_args.kwargs["use_search"] is True
    cfg.AI_SEARCH_PROVIDER = "firecrawl"
    assert client.call_search("persona and history", query="masked query") == "chat answer"
    search.assert_called_once_with("masked query")
    assert "source data" in chat.call_args.args[0]
    assert gemini.call_count == 1
    cfg.AI_SEARCH_PROVIDER = "unknown"
    with pytest.raises(ValueError):
        client.call_search("prompt", query="q")


@pytest.mark.parametrize("provider, backend_name, search_flag", [
    ("gemini", "_call_gemini", False),
    ("mlx", "_call_mlx", None),
    ("openai", "_call_openai_compatible", None),
])
def test_structured_generation_uses_chat_model_and_bounded_output(
    modules, monkeypatch, provider, backend_name, search_flag,
):
    cfg, client, _ = modules
    cfg.AI_PROVIDER = provider
    backend = MagicMock(return_value="structured answer")
    monkeypatch.setattr(client, backend_name, backend)

    assert client.call_structured("forecast", temperature=0.2, max_tokens=123) == "structured answer"
    assert backend.call_args.kwargs["model"] == "chat-test"
    assert backend.call_args.kwargs["temperature"] == 0.2
    assert backend.call_args.kwargs["max_tokens"] == 123
    if search_flag is not None:
        assert backend.call_args.kwargs["use_search"] is search_flag


def test_settings_save_mask_and_preserve(modules, monkeypatch, tmp_path):
    _, _, admin = modules
    app = Flask(__name__)
    app.register_blueprint(admin.admin_bp)
    client = app.test_client()
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-existing-test")
    groups = client.get("/admin/api/ai_env").get_json()
    items = {item["key"]: item for group in groups for item in group["items"]}
    assert items["FIRECRAWL_API_KEY"]["value"] == ""
    assert items["FIRECRAWL_API_KEY"]["is_set"] is True
    assert items["AI_SEARCH_PROVIDER"]["options"] == ["gemini:Gemini / Google検索", "firecrawl:Firecrawl"]
    saved = client.post("/admin/api/ai_env", json={
        "AI_SEARCH_PROVIDER": "firecrawl", "FIRECRAWL_API_KEY": "fc-new-test",
    })
    assert saved.status_code == 200 and saved.json["ok"]
    assert "fc-new-test" not in saved.get_data(as_text=True)
    assert client.post("/admin/api/ai_env", json={
        "FIRECRAWL_API_KEY": "", "AI_SEARCH_PROVIDER": "gemini",
    }).status_code == 200
    values = dotenv_values(tmp_path / ".env.local")
    assert values["FIRECRAWL_API_KEY"] == "fc-new-test"
    assert values["AI_SEARCH_PROVIDER"] == "gemini"
    before = (tmp_path / ".env.local").read_text()
    for bad in (
        {"AI_SEARCH_PROVIDER": "bogus"},
        {"FIRECRAWL_API_KEY": "x\nOTHER=value"},
        {"CONVERSATION_FORECAST_ENABLED": "sometimes"},
    ):
        assert client.post("/admin/api/ai_env", json=bad).status_code == 400
    assert (tmp_path / ".env.local").read_text() == before
