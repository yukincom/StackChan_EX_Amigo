from __future__ import annotations

import llm_client


def test_lab_provider_is_deterministic_and_offline(monkeypatch):
    monkeypatch.setattr(llm_client.config, "AI_PROVIDER", "lab")
    monkeypatch.setattr(llm_client.config, "AI_SUMMARY_PROVIDER", "lab")

    assert llm_client.call("secret prompt must not leave the process") == "仮想環境の固定応答です。"
    assert llm_client.call_search("search must stay offline") == "仮想環境の固定応答です。"
    assert llm_client.call_structured("structured prompt must stay offline") == "仮想環境の固定応答です。"
    assert llm_client.call_summary("summary must stay offline") == "仮想環境の固定応答です。"
