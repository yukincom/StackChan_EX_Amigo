"""Recorded-prefix replay, policy scoring and controlled reply comparisons."""
import time
from ..conversation_participation import Event, EventKind, Mode, Scene, Source, State, advance
from .context import DEFAULT_CONTEXT_CHARS, TextContext, Utterance, validate_names
from .reader import SCENE_KEYS, SceneOutputError, parse_scene

def replay_case(case, reader=None, *, backend="local", history_utterances=16,
                max_chars=DEFAULT_CONTEXT_CHARS):
    """Replay recorded context, NOT a free-running conversation simulation.

    Original assistant turns are history inputs, never predictions or gold.
    'oracle' explicitly supplies labelled scenes to test policy plumbing only.
    """
    if backend not in {"local", "oracle", "always_silent", "always_reply"}:
        raise ValueError("unknown backend")
    if backend == "local" and reader is None:
        raise ValueError("local backend requires a reader")
    validate_names(case.get("assistant_names"))
    state = State(Mode(case.get("initial_mode", "active")))
    context = TextContext(history_utterances, max_chars)
    rows = []
    for turn in case["turns"]:
        kind = EventKind(turn.get("kind", "speech"))
        row = {"id": turn["id"], "kind": kind.value, "text": turn.get("text", ""),
               "before": state.mode.value, "gold": turn.get("gold", {}),
               "context_ids": [], "context_chars": 0, "error": None}
        if kind is EventKind.SPEECH:
            item = Utterance(turn["id"], turn["role"], turn["text"])
            # Full OFF stops buffering and inference. Recorded assistant output
            # is historical context, not microphone feedback or a wake event.
            if state.mode is Mode.MIC_OFF:
                decision = advance(state, Event(kind, Scene(), turn.get("playback_active", False)))
            elif item.role == "assistant":
                context.append(item)
                rows.append({**row, "action": "context_only", "mode": state.mode.value})
                continue
            else:
                payload = context.payload(item, state, case["assistant_names"])
                row["context_ids"] = [i["id"] for i in payload["history"]]
                row["context_chars"] = sum(len(i["text"]) for i in payload["history"]) + len(item.text)
                start = time.monotonic()
                if item.role == "unknown":
                    scene = Scene()
                elif backend == "oracle":
                    # Labels enter ONLY this diagnostic mode, never a reader.
                    label = turn.get("gold", {}).get("scene")
                    if label is None:
                        scene = Scene()
                        row["error"] = "unlabelled_oracle_scene"
                    else:
                        scene = parse_scene({**label, "evidence_ids": []}, set())
                elif backend in {"always_silent", "always_reply"}:
                    scene = Scene(Source.HUMAN)
                else:
                    try:
                        scene, info = reader(payload)
                        if not isinstance(scene, Scene):
                            raise ValueError("reader must return a Scene")
                        row.update(info)
                    except Exception as exc:
                        # Invalid/failed model output is a failed observation,
                        # never counted as a successful silent prediction.
                        scene = Scene()
                        row["error"] = type(exc).__name__
                        if isinstance(exc, SceneOutputError):
                            row["rejected_prediction"] = exc.prediction
                row["elapsed_ms"] = round((time.monotonic() - start) * 1000)
                row["scene"] = {k: getattr(scene, k) for k in SCENE_KEYS}
                decision = advance(state, Event(kind, scene, turn.get("playback_active", False)))
                context.append(item)
        else:
            decision = advance(state, Event(kind, playback_active=turn.get("playback_active", False)))
            if kind is EventKind.MIC_OFF:
                context.clear()
        state = decision.state
        action = decision.action.value
        if backend in {"always_silent", "always_reply"} and kind is EventKind.SPEECH and state.mode is not Mode.MIC_OFF:
            action = "wait" if backend == "always_silent" else "reply"
        row.update(action=action, mode=state.mode.value, reason=decision.reason,
                   allow_conversation_history=(action == "reply"), stop_playback=decision.stop_playback)
        rows.append(row)
    return {"case_id": case["id"], "split": case["split"], "backend": backend,
            "assistant_names": list(case["assistant_names"]),
            "history_utterances": history_utterances, "max_chars": max_chars,
            "rows": rows, "metrics": score_rows(rows)}


def row_passes(row):
    gold = row.get("gold", {})
    return (gold.get("action") is not None and row["action"] == gold["action"]
            and (gold.get("mode") is None or row["mode"] == gold["mode"])
            and all(k not in gold or row.get(k) == gold[k]
                    for k in ("stop_playback", "allow_conversation_history"))
            and not row.get("error"))


def score_rows(rows):
    labelled = [r for r in rows if r.get("gold", {}).get("action") is not None]
    groups = {}
    for name in {r.get("gold", {}).get("category") for r in labelled} - {None}:
        items = [r for r in labelled if r["gold"].get("category") == name]
        groups[name] = {"correct": sum(row_passes(r) for r in items),
                        "total": len(items)}
    correct = sum(row_passes(r) for r in labelled)
    # All expected-silent speech counts, including new quoted-call categories.
    quiet_targets = [r for r in labelled if r.get("kind") == "speech" and r["gold"]["action"] == "wait"]
    reply_targets = [r for r in labelled if r["gold"]["action"] == "reply"]
    return {"labelled": len(labelled), "correct": correct,
            "errors": sum(bool(r.get("error")) for r in rows),
            "unscored": len(rows) - len(labelled), "by_category": groups,
            "false_interruptions": {"count": sum(r["action"] in {"reply", "announce_quiet"} for r in quiet_targets), "opportunities": len(quiet_targets)},
            "missed_replies": {"count": sum(r["action"] != "reply" or bool(r.get("error")) for r in reply_targets), "opportunities": len(reply_targets)},
            "scene_fields": score_fields(rows, "scene"),
            "structure_fields": score_fields(rows, "structure"),
            "whole_case_pass": bool(labelled) and correct == len(labelled)
                               and not any(r.get("error") for r in rows)}


def score_fields(rows, field):
    """Auxiliary structure agreement; never confuse action success with understanding."""
    scores = {}
    for row in rows:
        expected = row.get("gold", {}).get(field, {})
        actual = row.get(field, {})
        for key, value in expected.items():
            score = scores.setdefault(key, {"correct": 0, "total": 0, "missing": 0})
            score["total"] += 1
            score["missing"] += key not in actual
            score["correct"] += key in actual and actual[key] == value and not row.get("error")
    return scores

def reply_demos(case, run, reader, turn_ids):
    """Controlled gate comparison, not historical production output.

    Generate once using the replay-visible prefix; reuse the same wording
    when admitted to isolate participation from sampling variability.
    """
    turns = {t["id"]: t for t in case["turns"]}
    demos = []
    for row in run["rows"]:
        if row["id"] not in turn_ids:
            continue
        current = turns[row["id"]]
        if current.get("role") != "human" or row["before"] == "mic_off" or row.get("error"):
            continue
        clean = lambda t: {k: t[k] for k in ("id", "role", "text")}
        payload = {"history": [clean(turns[i]) for i in row["context_ids"]], "current": clean(current),
                   "assistant_names": list(case["assistant_names"]), "assistant_persona": case["assistant_persona"]}
        before, usage = reader.respond(payload)
        demos.append({"turn_id": row["id"], "input": current["text"],
                      "comparison": "same_generated_wording_with_or_without_participation_gate",
                      "before_ungated": before, "after_gated": before if row["action"] == "reply" else "",
                      "action": row["action"], "usage": usage,
                      "assistant_names": payload["assistant_names"], "assistant_persona": payload["assistant_persona"],
                      "note": "明示した名前・人格による簡易生成器。ai_handler等の本番経路/過去出力ではない。同じ生成文を参加判断あり/なしで比較。"})
    return demos
