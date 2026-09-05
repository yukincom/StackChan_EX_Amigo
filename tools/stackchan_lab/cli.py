"""Command line interface for the no-device maintenance lab."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import CONTRACT_VERSION, validate_firmware_route_presence
from .config_schema import plan_migrations, validate_all
from .doctor import doctor_summary, run_doctor
from .recovery import create_snapshot, restore_snapshot, verify_snapshot
from .scenario import run_smoke_scenario
from .support_bundle import create_support_bundle, verify_support_bundle
from .virtual_m5 import VirtualM5State, create_virtual_m5_app


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stackchan-lab")
    parser.add_argument("--version", action="version", version=f"stackchan-lab contract {CONTRACT_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="run offline, secret-safe diagnostics")
    doctor.add_argument("--repo", type=Path, default=Path.cwd())
    doctor.add_argument("--env-dir", type=Path)

    smoke = subparsers.add_parser("smoke", help="run a self-contained no-device scenario")
    smoke.add_argument("--repo", type=Path, default=Path.cwd())
    smoke.add_argument(
        "--allow-known-gaps",
        action="store_true",
        help="return success when required checks pass but documented gaps remain",
    )

    device = subparsers.add_parser("serve-device", help="serve the virtual M5Stack API")
    device.add_argument("--host", default="127.0.0.1")
    device.add_argument("--port", type=int, default=18080)
    device.add_argument("--server-url", default="")
    device.add_argument("--camera-upload-v2", action="store_true", default=True,
                        help=argparse.SUPPRESS)
    device.add_argument("--legacy-camera-upload", action="store_false", dest="camera_upload_v2",
                        help="reproduce older firmware that omits capture mode")

    snapshot = subparsers.add_parser("snapshot", help="create/verify/restore a checked snapshot")
    snapshot_sub = snapshot.add_subparsers(dest="snapshot_command", required=True)
    create = snapshot_sub.add_parser("create")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--include", action="append")
    verify = snapshot_sub.add_parser("verify")
    verify.add_argument("snapshot", type=Path)
    restore = snapshot_sub.add_parser("restore")
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("--target", type=Path, required=True)

    support = subparsers.add_parser("support", help="create/verify a secret-safe support bundle")
    support_sub = support.add_subparsers(dest="support_command", required=True)
    support_create = support_sub.add_parser("create")
    support_create.add_argument("--repo", type=Path, default=Path.cwd())
    support_create.add_argument("--env-dir", type=Path)
    support_create.add_argument("--trace-dir", type=Path)
    support_create.add_argument("--max-events", type=int, default=500)
    support_create.add_argument("--output", type=Path, required=True)
    support_verify = support_sub.add_parser("verify")
    support_verify.add_argument("bundle", type=Path)

    config_parser = subparsers.add_parser("config", help="validate or plan config schema changes")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_validate = config_sub.add_parser("validate")
    config_validate.add_argument("--repo", type=Path, default=Path.cwd())
    config_validate.add_argument("--env-dir", type=Path)
    config_plan = config_sub.add_parser("plan")
    config_plan.add_argument("--json-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        summary = doctor_summary(run_doctor(args.repo, args.env_dir))
        _print_json(summary)
        return 1 if summary["status"] == "error" else 0
    if args.command == "smoke":
        report = run_smoke_scenario(args.repo.resolve())
        route_errors = validate_firmware_route_presence(args.repo.resolve())
        report["firmware_route_presence_errors"] = route_errors
        if route_errors:
            report["status"] = "error"
        _print_json(report)
        if report["status"] == "error":
            return 1
        if report["status"] == "degraded" and not args.allow_known_gaps:
            return 2
        return 0
    if args.command == "serve-device":
        state = VirtualM5State(
            callback_server_url=args.server_url,
            camera_upload_v2=args.camera_upload_v2,
        )
        app = create_virtual_m5_app(state)
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
        return 0
    if args.command == "snapshot":
        if args.snapshot_command == "create":
            kwargs = {}
            if args.include:
                kwargs["include"] = args.include
            _print_json(create_snapshot(args.source, args.output, **kwargs))
            return 0
        if args.snapshot_command == "verify":
            _print_json(verify_snapshot(args.snapshot))
            return 0
        if args.snapshot_command == "restore":
            _print_json(restore_snapshot(args.snapshot, args.target))
            return 0
    if args.command == "support":
        if args.support_command == "create":
            _print_json(
                create_support_bundle(
                    args.repo,
                    args.output,
                    env_dir=args.env_dir,
                    trace_dir=args.trace_dir,
                    max_events=args.max_events,
                )
            )
            return 0
        if args.support_command == "verify":
            _print_json(verify_support_bundle(args.bundle))
            return 0
    if args.command == "config":
        if args.config_command == "validate":
            report = validate_all(args.repo, args.env_dir)
            _print_json(report)
            return 1 if report["status"] == "error" else 0
        if args.config_command == "plan":
            _print_json(plan_migrations(args.json_dir))
            return 0
    raise AssertionError("unreachable")
