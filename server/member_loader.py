# member_loader.py
"""member.jsonを読み込み、メンバーデータへのアクセスを提供する"""
import re
import json
from pathlib import Path

_MEMBER_JSON_PATH = Path(__file__).resolve().parent / "json" / "member.json"
MASTER_LABEL = "マスター"
MASTER_MASK_LABEL = "[マスター]"
OTHER_LABEL = "その他"
_FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def _default_master() -> dict:
    return {
        "name": "",
        "notes": "",
        "interests": [],
        "line_user_id": "",
        "discord_user_id": "",
    }


def _normalize_data(data: dict | None) -> dict:
    raw = data if isinstance(data, dict) else {}

    master = raw.get("master")
    if not isinstance(master, dict):
        master = _default_master()

    family = raw.get("family", [])
    if not isinstance(family, list):
        family = []

    friends = raw.get("friends", [])
    if not isinstance(friends, list):
        friends = []

    return {
        "master": master,
        "family": family,
        "friends": friends,
    }


def load_member_data() -> dict:
    if not _MEMBER_JSON_PATH.exists():
        return {"master": _default_master(), "family": [], "friends": []}
    with open(_MEMBER_JSON_PATH, "r", encoding="utf-8") as f:
        return _normalize_data(json.load(f))


def get_primary_name(member: dict) -> str:
    """name の最初の1つを返す（表示・照合の基準名）"""
    name = member.get("name", "")
    return name[0] if isinstance(name, list) else name


def get_all_names(member: dict) -> list:
    """name の全バリアントをリストで返す（マスク用）"""
    name = member.get("name", "")
    if isinstance(name, list):
        return [n for n in name if n]
    return [name] if name else []


def get_primary_call(member: dict) -> str:
    """call の最初の1つを返す（通知メッセージ等で使う表示名）"""
    call = member.get("call", "")
    return call[0] if isinstance(call, list) else call


def _to_fullwidth_digits(text: str) -> str:
    return str(text).translate(_FULLWIDTH_DIGITS)


def _label_variants(label: str) -> list[str]:
    """ラベルの揺れを吸収するための候補を返す"""
    bare = label.removeprefix("[").removesuffix("]")
    bare_fullwidth = _to_fullwidth_digits(bare)
    variants = {
        label,
        bare,
        f"[{bare_fullwidth}]",
        f"［{bare}］",
        f"［{bare_fullwidth}］",
        bare_fullwidth,
    }
    return [variant for variant in variants if variant]


def find_by_user_id(user_id: str, data: dict | None = None) -> dict | None:
    if not user_id:
        return None
    members = load_member_data() if data is None else data
    all_members = [members.get("master", _default_master())] + members.get("family", [])
    for member in all_members:
        if member.get("line_user_id") == user_id:
            return member
        if member.get("discord_user_id") == user_id:
            return member
    return None


def get_call_name_by_user_id(user_id: str, default: str = "", data: dict | None = None) -> str:
    member = find_by_user_id(user_id, data=data)
    if not member:
        return default
    return get_primary_call(member) or get_primary_name(member) or default


def get_speaker_patterns(data: dict | None = None) -> list:
    """
    話者判定用パターンを返す
    [(patterns_list, call_name, speaker_id), ...]
    例: (["あら", "かしら"], "お母さん", "family1")
    """
    members = load_member_data() if data is None else data
    result = []
    for i, member in enumerate(members.get("family", []), 1):
        patterns = [p for p in member.get("speech_patterns", []) if p]
        call = get_primary_call(member)
        result.append((patterns, call, f"family{i}"))
    return result


def get_family_call_map(data: dict | None = None) -> dict:
    """speaker_id → call_name のマッピングを返す"""
    members = load_member_data() if data is None else data
    return {
        f"family{i}": get_primary_call(member)
        for i, member in enumerate(members.get("family", []), 1)
    }


def get_speaker_label_map(data: dict | None = None) -> dict:
    """speaker_id → 表示ラベル のマッピングを返す"""
    members = load_member_data() if data is None else data
    return {
        **get_family_call_map(members),
        "other": OTHER_LABEL,
        "master": MASTER_LABEL,
    }


def get_speaker_mask_label_map(data: dict | None = None) -> dict:
    """speaker_id → AI向けマスクラベル のマッピングを返す"""
    members = load_member_data() if data is None else data
    labels = {"other": OTHER_LABEL, "master": MASTER_MASK_LABEL}
    for i, _member in enumerate(members.get("family", []), 1):
        labels[f"family{i}"] = f"[家族{i}]"
    for i, _member in enumerate(members.get("friends", []), 1):
        labels[f"friend{i}"] = f"[友達{i}]"
    return labels


def get_mask_replacements(data: dict | None = None) -> dict:
    """mask_names用: {実名: ラベル}"""
    members = load_member_data() if data is None else data
    master = members.get("master", _default_master())
    family = members.get("family", [])
    friends = members.get("friends", [])
    rep = {}
    for name in get_all_names(master):
        rep[name] = MASTER_MASK_LABEL
    for i, member in enumerate(family, 1):
        for name in get_all_names(member):
            rep[name] = f"[家族{i}]"
    for i, friend in enumerate(friends, 1):
        name = friend.get("name", "")
        if name and name != "友達の呼び名":
            rep[name] = f"[友達{i}]"
    return rep


def get_unmask_replacements(data: dict | None = None) -> dict:
    """unmask_names用: {ラベル: 復元表示名}"""
    members = load_member_data() if data is None else data
    master = members.get("master", _default_master())
    family = members.get("family", [])
    friends = members.get("friends", [])
    rep = {}
    master_name = get_primary_name(master)
    if master_name:
        for label in _label_variants(MASTER_MASK_LABEL):
            rep[label] = master_name
    for i, member in enumerate(family, 1):
        name = get_primary_call(member) or get_primary_name(member)
        if name:
            for label in _label_variants(f"[家族{i}]"):
                rep[label] = name
    for i, friend in enumerate(friends, 1):
        name = friend.get("name", "")
        if name and name != "友達の呼び名":
            for label in _label_variants(f"[友達{i}]"):
                rep[label] = name
    return rep


def has_line_users() -> bool:
    members = load_member_data()
    for member in [members.get("master", _default_master())] + members.get("family", []):
        if member.get("line_user_id"):
            return True
    return False


def has_discord_users() -> bool:
    members = load_member_data()
    for member in [members.get("master", _default_master())] + members.get("family", []):
        if member.get("discord_user_id"):
            return True
    return False


def _to_hira(s: str) -> str:
    return ''.join(
        chr(ord(c) - 0x60) if 'ァ' <= c <= 'ン' else c
        for c in s
    )


def mask_names(text: str, data: dict | None = None) -> str:
    """家族・マスターの名前をラベルに置換（アシスタント名は対象外）"""
    replacements = {}
    for name, label in get_mask_replacements(data).items():
        replacements[name] = label
        replacements[_to_hira(name)] = label

    # 助詞や敬称が後続しても確実に隠すため、境界判定よりも長い名前から順に
    # 文字列置換する。アシスタント名は replacement 対象に含めない。
    for name in sorted({n for n in replacements if n}, key=len, reverse=True):
        text = text.replace(name, replacements[name])
    return text


def unmask_names(text: str, data: dict | None = None) -> str:
    """ラベルを元の名前に戻す"""
    text = re.sub(r"\[/?section\]", "", text, flags=re.IGNORECASE)
    replacements = get_unmask_replacements(data)
    for label in sorted({label for label in replacements if label}, key=len, reverse=True):
        text = text.replace(label, replacements[label])
    return text
