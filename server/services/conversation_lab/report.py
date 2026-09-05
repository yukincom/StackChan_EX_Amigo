"""Escaped local reports, kept separate from inference and replay."""
import html
import json
from pathlib import Path
from .evaluation import row_passes

def check_report_path(path: Path):
    if path.exists() and not path.is_dir():
        raise ValueError("report output must be a directory")
    for name in ("report.json", "report.html"):
        if (path / name).exists():
            raise ValueError("report already exists; choose a new output directory")

def save_report(path: Path, report):
    """Explicit private output; refuse to overwrite prior evaluation evidence."""
    check_report_path(path)
    path.mkdir(parents=True, exist_ok=True)
    content = ["<!doctype html><meta charset='utf-8'><title>対話テキストラボ</title>",
               "<style>body{font:16px sans-serif;max-width:1100px;margin:2em auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:.5em}pre{white-space:pre-wrap}.bad{background:#ffe5e5}</style>",
               "<h1>対話テキストラボ</h1><p>実機・音声なし。正解は推定注釈。元GPTの応答は正解扱いしない。oracleは意味理解の評価ではありません。</p>"]
    for run in report["runs"]:
        content.append(f"<h2>{html.escape(run['case_id'])} / {run['backend']} / 直前{run['history_utterances']}発話</h2>")
        content.append("<p>登録名: " + html.escape(" / ".join(run["assistant_names"])) + "</p>")
        content.append("<pre>" + html.escape(json.dumps(run["metrics"], ensure_ascii=False, indent=2)) + "</pre>")
        content.append("<table><tr><th>ID</th><th>入力（原ログ/抜粋）</th><th>判断</th><th>期待</th><th>状態・保持数</th></tr>")
        for row in run["rows"]:
            gold = row.get("gold", {}).get("action")
            bad = row.get("error") or (gold is not None and not row_passes(row))
            values = [row["id"], row["text"] or row["kind"], row["action"] + (" / " + row["error"] if row.get("error") else ""),
                      (gold + " / " + row["gold"].get("mode", "任意")) if gold else "採点対象外",
                      f"{row['mode']} / {len(row['context_ids'])}"]
            content.append(("<tr class='bad'>" if bad else "<tr>") + "".join("<td>" + html.escape(str(v)) + "</td>" for v in values) + "</tr>")
        content.append("</table>")
        for demo in run.get("reply_demos", []):
            content.append("<h3>Qwen比較: " + html.escape(demo["turn_id"]) + "</h3><p>" + html.escape(demo["note"]) + "</p>")
            for name, value in (("入力", demo["input"]), ("参加判断なし", demo["before_ungated"]),
                                ("参加判断あり", demo["after_gated"] or "（返答しない）")):
                content.append("<p>" + name + "</p><pre>" + html.escape(value) + "</pre>")
    (path / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (path / "report.html").write_text("\n".join(content) + "\n", encoding="utf-8")
