"""Firecrawl v2 search adapter. No SDK, crawling, or implicit provider fallback."""
import json
import os

import requests


def search_context(query: str) -> str:
    query = query.strip()
    if not query:
        raise ValueError("Firecrawlの検索語がありません")
    key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not key:
        raise ValueError("Firecrawl APIキーをAI設定画面で設定してください")
    try:
        response = requests.post(
            "https://api.firecrawl.dev/v2/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": query, "sources": ["web"]},
            timeout=(5, 65),
            allow_redirects=False,
        )
    except requests.RequestException:
        raise RuntimeError("Firecrawlへの接続に失敗しました") from None
    # Never echo response bodies, request headers, keys, or query text in errors.
    with response:
        if response.status_code != 200:
            raise RuntimeError(f"Firecrawl検索に失敗しました（HTTP {response.status_code}）")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("Firecrawlの応答形式が不正です") from None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("Firecrawl検索が成功しませんでした")
    data = payload.get("data")
    results = data.get("web") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise RuntimeError("Firecrawlの検索結果形式が不正です")
    sources = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            continue
        sources.append({k: item.get(k, "") for k in ("title", "url", "description")})
    return json.dumps(sources, ensure_ascii=False) if sources else "検索結果はありません。"
