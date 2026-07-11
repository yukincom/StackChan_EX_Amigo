"""パス・ID・URL の安全化ヘルパ（SSRF / パストラバーサル対策）"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

# キャッシュ名・song 名: noise_check, vision_ai_trigger など
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# voice_id: 174000_ab12..., en_..., mix_..., cache_ok_english
_SAFE_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")


def safe_id(value: str, *, max_len: int = 128) -> str | None:
    """ファイル名用の安全な ID。パス区切りや .. を拒否。"""
    v = (value or "").strip()
    if not v or len(v) > max_len:
        return None
    if "/" in v or "\\" in v or ".." in v:
        return None
    if not _SAFE_ID_RE.fullmatch(v):
        return None
    return v


def safe_voice_id(value: str) -> str | None:
    v = (value or "").strip()
    if not v or len(v) > 192:
        return None
    if "/" in v or "\\" in v or ".." in v:
        return None
    if not _SAFE_VOICE_ID_RE.fullmatch(v):
        return None
    return v


def resolve_under(base_dir: Path | str, *parts: str) -> Path:
    """
    base_dir 配下に収まる Path を返す。
    はみ出す場合は ValueError。
    """
    base = Path(base_dir).expanduser().resolve()
    candidate = base.joinpath(*parts).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path escapes base directory: {candidate}") from exc
    return candidate


def path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False
