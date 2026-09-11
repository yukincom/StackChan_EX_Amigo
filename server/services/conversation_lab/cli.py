"""Explicit offline CLI; does not start app, TTS, schedulers or devices."""
import argparse
import hashlib
import json
from pathlib import Path
from ..conversation_participation import Event, EventKind, Mode, State, advance
from .context import DEFAULT_CONTEXT_CHARS, TextContext, Utterance, validate_names
from .reader import LocalSceneReader, PROMPT
from .model_identity import resolve_model_identity
from .structured_reader import StructuredSceneReader, STRUCTURED_PROMPT
from .cases import load_cases
from .evaluation import replay_case, reply_demos
from .report import check_report_path, save_report

def interactive(reader, *, history_utterances=16, max_chars=DEFAULT_CONTEXT_CHARS,
                aliases=None):
    """Manual input checks participation only; no transcript/report persistence."""
    state, context = State(), TextContext(history_utterances, max_chars)
    validate_names(aliases)
    commands = {"/touch": EventKind.FACE_TOUCH, "/gap": EventKind.TURN_BOUNDARY,
                "/off": EventKind.MIC_OFF, "/on": EventKind.MIC_ON}
    print("登録名: " + " / ".join(aliases))
    print("参加判断のみ（返答本文・音声・保存なし）。/touch /gap /off /on /quit")
    count = 0
    while True:
        try:
            text = input("> ")
        except EOFError:
            return
        if text == "/quit":
            return
        if not text.strip():
            continue
        if text in commands:
            kind = commands[text]
            decision = advance(state, Event(kind))
            if kind is EventKind.MIC_OFF:
                context.clear()
        elif state.mode is Mode.MIC_OFF:
            print("mic_off: 入力は判定・保持しません")
            continue
        else:
            count += 1
            current = Utterance(f"u{count}", "human", text)
            try:
                scene, _ = reader(context.payload(current, state, aliases))
                decision = advance(state, Event(EventKind.SPEECH, scene))
                context.append(current)
            except Exception as exc:
                print("判定失敗: " + type(exc).__name__)
                continue
        state = decision.state
        print(json.dumps({"action": decision.action.value, "mode": state.mode.value,
                          "reason": decision.reason}, ensure_ascii=False))


def main(argv=None, *, model_config=None, model_config_loader=None):
    parser = argparse.ArgumentParser(description="Offline text participation lab; no firmware/app/TTS/memory startup")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cases", type=Path)
    source.add_argument("--interactive", action="store_true", help="type text/control events; no file output")
    parser.add_argument("--name", action="append", help="assistant alias for manual input (repeatable)")
    parser.add_argument("--backend", choices=["local", "oracle", "always_silent", "always_reply"], default="oracle")
    parser.add_argument("--reader", choices=["baseline", "structured"], default="baseline",
                        help="opt-in scene reader experiment; baseline remains the default")
    parser.add_argument("--model", help="existing local model ID; never switches the production server")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/chat/completions")
    parser.add_argument("--history", default="16", help="prior utterances; comma-separated windows for comparison, e.g. 0,4,8,16,32")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_CONTEXT_CHARS)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--demo-turn", action="append", default=[], help="Qwen text comparison for an explicit turn ID; one case/window only")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    parser.add_argument("--output", type=Path, help="private replay report directory; never uploaded")
    args = parser.parse_args(argv)
    def selected_identity():
        configured = model_config
        if args.model is None and configured is None and model_config_loader is not None:
            configured = model_config_loader()
        return resolve_model_identity(args.model, configured)

    if args.reader != "baseline" and args.backend != "local":
        parser.error("--reader structured requires --backend local")
    reader_type = StructuredSceneReader if args.reader == "structured" else LocalSceneReader
    prompt = STRUCTURED_PROMPT if args.reader == "structured" else PROMPT
    if args.name and not args.interactive:
        parser.error("--name is for interactive input only; replay identities must be explicit in each case")
    if args.interactive and not args.name:
        parser.error("manual input requires explicit --name; production character settings are not loaded")
    if not args.interactive and args.output is None:
        parser.error("replay requires --output")
    if args.output is not None:
        check_report_path(args.output)
    windows = [int(w) for w in args.history.split(",")]
    for window in windows:
        TextContext(window, args.max_chars)  # validate ALL widths before any inference
    if args.interactive:
        if args.backend != "local" or len(windows) != 1 or args.output is not None:
            parser.error("manual input needs --backend local, one history width and no --output")
        identity = selected_identity()
        interactive(reader_type(identity.model, args.endpoint), history_utterances=windows[0],
                    max_chars=args.max_chars, aliases=args.name)
        return 0
    suite = load_cases(args.cases)
    selected = [c for c in suite["cases"] if (args.split == "all" or c["split"] == args.split)
                and (not args.case_ids or c["id"] in args.case_ids)]
    if not selected:
        parser.error("no matching cases")
    if args.demo_turn and (args.backend != "local" or len(selected) != 1 or len(windows) != 1
                          or set(args.demo_turn) - {t["id"] for t in selected[0]["turns"]}):
        parser.error("reply demos require local, one case/window and existing turn IDs")
    if args.demo_turn and not selected[0].get("assistant_persona"):
        parser.error("reply demos require explicit assistant_persona in the case; no generic Qwen persona fallback")
    identity = selected_identity() if args.backend == "local" else None
    reader = reader_type(identity.model, args.endpoint) if identity else None
    if reader is not None:
        prompt = reader.prompt
    report = {"schema_version": 1, "mode": "recorded_context_replay",
              "suite_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              "reader": args.reader, "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
              "engine_sha256": hashlib.sha256(b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest(),
              "reducer_sha256": hashlib.sha256((Path(__file__).parent.parent / "conversation_participation.py").read_bytes()).hexdigest(),
              "model": identity.model if identity else args.model,
              "model_series": identity.series if identity else None, "runs": []}
    for window in windows:
        for case in selected:
            run = replay_case(case, reader, backend=args.backend, history_utterances=window, max_chars=args.max_chars)
            if args.demo_turn:
                run["reply_demos"] = reply_demos(case, run, reader, set(args.demo_turn))
            report["runs"].append(run)
            print(json.dumps({k: run[k] for k in ("case_id", "backend", "history_utterances", "metrics")}, ensure_ascii=False), flush=True)
    save_report(args.output, report)
    return 0 if all(r["metrics"]["whole_case_pass"] for r in report["runs"]) else 1
