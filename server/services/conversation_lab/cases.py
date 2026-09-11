"""Private case schema and source-group split validation."""
import json
from pathlib import Path
from ..conversation_participation import Action, EventKind, Mode
from .context import Utterance, validate_names

def load_cases(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError("expected a nonempty version 1 case suite")
    seen_cases, splits = set(), {}
    for case in data["cases"]:
        if not case.get("id") or case["id"] in seen_cases:
            raise ValueError("case IDs must be unique")
        seen_cases.add(case["id"])
        if case.get("split") not in {"dev", "holdout"}:
            raise ValueError("case split must be dev or holdout")
        group = case.get("source_group")
        if not isinstance(group, str) or not group:
            raise ValueError("source_group is required to prevent split leakage")
        if group in splits and splits[group] != case["split"]:
            raise ValueError("same source conversation appears in both splits")
        splits[group] = case["split"]
        validate_names(case.get("assistant_names"))
        if "assistant_persona" in case and (not isinstance(case["assistant_persona"], str) or not case["assistant_persona"].strip()):
            raise ValueError("assistant_persona must be nonempty text when supplied")
        ids = set()
        if not case.get("turns"):
            raise ValueError("case must contain turns")
        for turn in case["turns"]:
            if not isinstance(turn.get("id"), str) or not turn["id"] or turn["id"] in ids:
                raise ValueError("turn IDs must be unique within a case")
            ids.add(turn["id"])
            kind = turn.get("kind", "speech")
            EventKind(kind)
            if kind == "speech":
                Utterance(turn["id"], turn["role"], turn["text"])
            elif turn.get("text") or turn.get("role"):
                raise ValueError("controls cannot smuggle speech")
            if type(turn.get("playback_active", False)) is not bool:
                raise ValueError("playback_active must be boolean")
            gold = turn.get("gold", {})
            if not isinstance(gold, dict) or set(gold) - {"action", "mode", "category", "scene", "structure", "annotation", "stop_playback", "allow_conversation_history"}:
                raise ValueError("unknown gold fields")
            structure = gold.get("structure", {})
            if not isinstance(structure, dict) or set(structure) - {"name_use", "reply_to_id"}:
                raise ValueError("unknown gold structure fields")
            if "name_use" in structure and structure["name_use"] not in {"direct_call", "mention", "quoted_call", "other_call", "none"}:
                raise ValueError("invalid gold name use")
            parent = structure.get("reply_to_id")
            if parent is not None and (not isinstance(parent, str) or parent not in ids - {turn["id"]}):
                raise ValueError("gold reply-to must reference a prior turn")
            if any(k in gold for k in ("mode", "stop_playback", "allow_conversation_history")) and gold.get("action") is None:
                raise ValueError("state/side-effect expectations require an action")
            for flag in ("stop_playback", "allow_conversation_history"):
                if flag in gold and type(gold[flag]) is not bool:
                    raise ValueError("gold side-effect flags must be boolean")
            if gold.get("action") is not None:
                Action(gold["action"])
            if gold.get("mode") is not None:
                Mode(gold["mode"])
    return data
