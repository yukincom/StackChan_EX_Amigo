# memory_manager.py
"""basic-memory版 記憶システム（Markdown形式）"""

from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import config
from member_loader import get_primary_name, load_member_data, mask_names

MEMORY_DIR = Path(config.MEMORY_DIR)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# today.md の全読み書きを直列化（chat / weather / batch / vision）
_TODAY_LOCK = threading.RLock()
_TODAY_BASENAME = "today"


def today_md_path() -> Path:
    return MEMORY_DIR / f"{_TODAY_BASENAME}.md"


def _read_today_unlocked() -> str:
    path = today_md_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_today_unlocked(text: str) -> None:
    path = today_md_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_today_unlocked(text: str) -> None:
    path = today_md_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def read_today() -> str:
    """today.md をロック付きで読む。"""
    with _TODAY_LOCK:
        return _read_today_unlocked()


def append_today(text: str) -> None:
    """today.md に追記（ロック付き）。"""
    if not text:
        return
    with _TODAY_LOCK:
        _append_today_unlocked(text)


def replace_today(text: str) -> None:
    """today.md を全文置換（ロック付き）。"""
    with _TODAY_LOCK:
        _write_today_unlocked(text)


def update_today(mutator: Callable[[str], str]) -> str:
    """
    today.md の read-modify-write をロック内で行う。

    mutator には現在の全文が渡り、戻り値で上書きする。
    LLM など重い処理は mutator の外で行い、ここには I/O だけ載せる。
    """
    with _TODAY_LOCK:
        current = _read_today_unlocked()
        new_content = mutator(current)
        if new_content is None:
            new_content = current
        if new_content != current:
            _write_today_unlocked(new_content)
        return new_content


class RobotMemory:
    """
    Markdown形式の記憶システム。

    ファイル構成:
    MEMORY_DIR/
    └── today.md    # ゲーム進行 + 会話履歴
    """

    def __init__(self):
        self.memory_dir = MEMORY_DIR

    # ─────────────────────────────
    # ファイル読み書き
    # ─────────────────────────────
    def _read(self, name: str) -> str:
        if name == _TODAY_BASENAME:
            return read_today()
        path = self.memory_dir / f"{name}.md"
        if not path.exists():
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _append(self, name: str, text: str) -> None:
        if name == _TODAY_BASENAME:
            append_today(text)
            return
        path = self.memory_dir / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)

    # ─────────────────────────────
    # 会話の記憶
    # ─────────────────────────────
    def add_conversation(self, speaker, user_text, ai_response, speaker_label=None):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        label = speaker_label if speaker_label else speaker
        label = mask_names(label)
        masked_user = mask_names(user_text)
        masked_ai = mask_names(ai_response)
        lines = [f"\n### {timestamp}"]
        if masked_user:
            lines.append(f"- {label}: {masked_user}")
        if masked_ai:
            lines.append(f"- {config.ASSISTANT_NAME}: {masked_ai}")
        entry = "\n".join(lines) + "\n"
        self._append("today", entry)

    def get_recent_conversations(self, limit=None, content: str | None = None):
        """today.mdから最近の会話を取得。content 指定時は再読込しない。"""
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = None
            else:
                if limit <= 0:
                    return []

        if content is None:
            content = self._read("today")
        if not content:
            return []

        blocks = re.split(r"\n### ", content)
        conversations = []

        for block in blocks:
            lines = block.strip().split("\n")
            if not lines:
                continue

            user_text = ""
            ai_text = ""
            timestamp = ""

            for line in lines:
                if re.match(r"\d{4}-\d{2}-\d{2}", line):
                    timestamp = line.strip()
                elif line.startswith("- ") and ": " in line:
                    parts = line[2:].split(": ", 1)
                    if len(parts) == 2:
                        role, text = parts
                        if role == config.ASSISTANT_NAME:
                            ai_text = text
                        else:
                            user_text = text

            if user_text and ai_text:
                conversations.append({
                    "timestamp": timestamp,
                    "user": user_text,
                    "assistant": ai_text,
                })

        return conversations[-limit:] if limit is not None else conversations

    # ─────────────────────────────
    # コンテキスト取得（AI呼び出し用）
    # ─────────────────────────────
    def get_context(self, query=None, recent_turns=None):
        """AI呼び出し用のコンテキストを構築"""
        # 同一スナップショットから recent / ゲーム進行を取る
        with _TODAY_LOCK:
            today_md = _read_today_unlocked()
            recent_limit = config.AI_RECENT_TURNS if recent_turns is None else recent_turns
            recent = self.get_recent_conversations(limit=recent_limit, content=today_md)
            game_text = self._extract_section(today_md, "ゲーム進行")

        # ペルソナは.envから
        persona = config.ASSISTANT_PERSONA

        members = load_member_data()
        master = members.get("master", {})
        raw_interests = master.get("interests", [])
        interests = "、".join(raw_interests) if isinstance(raw_interests, list) else raw_interests
        notes = master.get("notes", "")

        # 家族構成
        family = {}
        master_name = get_primary_name(master)
        if master_name:
            family[master_name] = master.get("notes", "")
        for member in members.get("family", []):
            name = get_primary_name(member)
            if name:
                family[name] = member.get("notes", "")

        game_progress = []
        for line in game_text.split("\n"):
            if ": " in line and line.startswith("- "):
                parts = line[2:].split(": ", 1)
                if len(parts) == 2:
                    game_progress.append({
                        "game_name": parts[0].strip(),
                        "progress": parts[1].strip(),
                    })

        return {
            "persona": persona,
            "family": family,
            "recent_conversations": recent,
            "game_progress": game_progress,
            "notes": notes,
            "interests": interests,
        }

    def _extract_section(self, content, section_name):
        """## セクション名 の内容を抽出"""
        pattern = rf"## {re.escape(section_name)}\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    # ─────────────────────────────
    # バッチ処理用
    # ─────────────────────────────
    def get_daily_summary(self):
        """今日の会話を取得"""
        today = datetime.now().strftime("%Y-%m-%d")
        recent = self.get_recent_conversations(limit=100)
        return [c for c in recent if c.get("timestamp", "").startswith(today)]
