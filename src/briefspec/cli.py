from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from briefspec import __version__
from briefspec.adapters import normalize_event
from briefspec.bundle import build_delivery_bundle, deliver_bundle, export_delivery_formats
from briefspec.capabilities import runtime_capabilities
from briefspec.config import briefspec_home, config_template, load_config
from briefspec.delivery import load_delivery, validate_delivery
from briefspec.diagnostics import doctor_all_scopes, doctor_runtime
from briefspec.errors import BriefSpecError, InstallConflict
from briefspec.frames import render_frame
from briefspec.harnesses import detected_harnesses
from briefspec.hooks import emit_diagnostics, process_event, read_hook_payload, render_decision
from briefspec.installers import install_runtime, install_runtimes, uninstall_runtime
from briefspec.markdown import detect_kind, validate_checkpoint, validate_outcome
from briefspec.models import CheckpointMode, Runtime, VerificationLevel, WorkType
from briefspec.renderers import renderer_capabilities, setup_renderers
from briefspec.state import atomic_write, list_sessions, prune_sessions, reset_session
from briefspec.verification import verify_target
from briefspec.work_types import classify_task, type_profile, types_document


def _runtimes(value: str) -> list[Runtime]:
    return list(Runtime) if value == "all" else [Runtime(value)]


def _detect_runtime(payload: dict[str, Any]) -> Runtime:
    """Resolve runtime with explicit, stable-ID, marker, then default precedence."""
    explicit = str(payload.get("runtime") or payload.get("provider") or "").lower()
    if explicit in {item.value for item in Runtime}:
        return Runtime(explicit)

    stable_ids = (
        (Runtime.CODEX, payload.get("codex_thread_id") or os.environ.get("CODEX_THREAD_ID")),
        (
            Runtime.CLAUDE,
            payload.get("claude_session_id") or os.environ.get("CLAUDE_SESSION_ID"),
        ),
        (Runtime.COPILOT, payload.get("copilot_session_id")),
        (Runtime.KIMI, payload.get("kimi_session_id")),
        (Runtime.GROK, payload.get("grok_session_id")),
        (Runtime.OMP, payload.get("omp_session_id")),
    )
    for runtime, identifier in stable_ids:
        if identifier:
            return runtime

    claude_marker = any(name.startswith("CLAUDE_CODE") for name in os.environ) or bool(
        os.environ.get("CLAUDE_PLUGIN_ROOT")
    )
    if claude_marker:
        return Runtime.CLAUDE
    if any(name.startswith("COPILOT") for name in os.environ):
        return Runtime.COPILOT
    if os.environ.get("KIMI_CODE_HOME"):
        return Runtime.KIMI
    if os.environ.get("GROK_HOME"):
        return Runtime.GROK
    if os.environ.get("PI_CODING_AGENT_DIR") or os.environ.get("OMP_PROFILE"):
        return Runtime.OMP
    return Runtime.CODEX


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _print_result(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True, default=str))
        return
    if isinstance(value, list):
        for item in value:
            _print_result(item, False)
        return
    if isinstance(value, dict) and "operations" in value:
        print(f"{value.get('runtime', 'briefspec')}: {value.get('scope', '')}")
        for operation in value["operations"]:
            print(f"  {operation['action']:12} {operation['path']} — {operation['detail']}")
        for warning in value.get("warnings", []):
            print(f"  WARN         {warning}")
        return
    if isinstance(value, dict) and "checks" in value:
        scope = f" ({value['scope']} scope)" if value.get("scope") else ""
        label = value.get("runtime") or value.get("target") or "briefspec"
        print(f"{label}: {value['status']}{scope}")
        for check in value["checks"]:
            print(f"  {check['status']:4} {check['name']}: {check['detail']}")
            if check.get("remediation"):
                print(f"       → {check['remediation']}")
        return
    print(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brief-spec",
        description="Predictable, evidence-backed handoffs for AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("install", "uninstall"):
        command = commands.add_parser(name)
        command.add_argument("runtime", choices=[item.value for item in Runtime] + ["all"])
        command.add_argument("--scope", choices=["user", "project"], default="user")
        command.add_argument("--project", type=Path)
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--json", action="store_true")

    setup = commands.add_parser("setup")
    setup.add_argument("runtime", choices=[item.value for item in Runtime] + ["all"])
    setup.add_argument("--scope", choices=["user", "project"], default="user")
    setup.add_argument("--project", type=Path)
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument(
        "--require",
        help="comma-separated harnesses that must be detected before setup",
    )
    setup.add_argument("--json", action="store_true")

    doctor = commands.add_parser("doctor")
    doctor.add_argument(
        "runtime",
        nargs="?",
        choices=[item.value for item in Runtime] + ["all"],
        default="all",
    )
    doctor.add_argument("--scope", choices=["auto", "user", "project"], default="auto")
    doctor.add_argument("--project", type=Path)
    doctor.add_argument("--probe", action="store_true")
    doctor.add_argument("--fix", action="store_true")
    doctor.add_argument("--replace-modified", action="store_true")
    doctor.add_argument("--dry-run", action="store_true")
    doctor.add_argument("--all-scopes", action="store_true")
    doctor.add_argument("--json", action="store_true")

    capabilities = commands.add_parser("capabilities")
    capabilities.add_argument(
        "runtime",
        choices=[item.value for item in Runtime] + ["all"],
        default="all",
        nargs="?",
    )
    capabilities.add_argument("--json", action="store_true")

    types = commands.add_parser("types")
    type_commands = types.add_subparsers(dest="types_command", required=True)
    type_list = type_commands.add_parser("list")
    type_list.add_argument("--json", action="store_true")
    type_show = type_commands.add_parser("show")
    type_show.add_argument("type", choices=[item.value for item in WorkType])
    type_show.add_argument("--json", action="store_true")

    classify = commands.add_parser("classify")
    classify.add_argument("input")
    classify.add_argument("--type", choices=[item.value for item in WorkType])
    classify.add_argument("--subject")
    classify.add_argument("--json", action="store_true")

    frame = commands.add_parser("frame")
    frame.add_argument("request", help="BriefSpecFrameRequest/v1 JSON path or - for stdin")
    frame.add_argument("--output", required=True, type=Path)
    frame.add_argument("--force", action="store_true")
    frame.add_argument("--json", action="store_true")

    validate = commands.add_parser("validate")
    validate.add_argument("kind_or_path", nargs="?", default="-")
    validate.add_argument("path", nargs="?")
    validate.add_argument("--mode", choices=[item.value for item in CheckpointMode])
    validate.add_argument("--strict", action="store_true")
    validate.add_argument("--json", action="store_true")

    export = commands.add_parser("export")
    export.add_argument("input")
    export.add_argument(
        "--formats",
        default="markdown,json,html",
        help="comma-separated core or installed renderer formats",
    )
    export.add_argument("--output-dir", required=True, type=Path)
    export.add_argument("--force", action="store_true")
    export.add_argument("--runtime", default="unknown")
    export.add_argument("--harness")
    export.add_argument("--session-ref")
    export.add_argument("--host-version")
    export.add_argument("--adapter-version")
    export.add_argument("--source-revision")
    export.add_argument("--model")
    export.add_argument("--model-provider")
    export.add_argument("--created-at")
    export.add_argument("--pdf-page-format", choices=["A4", "Letter"], default="A4")
    export.add_argument("--audio-provider", choices=["macos", "openai"], default="macos")
    export.add_argument("--voice")
    export.add_argument("--rate", type=int, default=190)
    export.add_argument("--consent-network", action="store_true")
    export.add_argument("--json", action="store_true")

    bundle = commands.add_parser("bundle")
    bundle.add_argument("input")
    bundle.add_argument("--output", required=True, type=Path)
    bundle.add_argument("--formats", default="markdown,json,html")
    bundle.add_argument("--force", action="store_true")
    bundle.add_argument("--runtime", default="unknown")
    bundle.add_argument("--harness")
    bundle.add_argument("--session-ref")
    bundle.add_argument("--host-version")
    bundle.add_argument("--adapter-version")
    bundle.add_argument("--source-revision")
    bundle.add_argument("--model")
    bundle.add_argument("--model-provider")
    bundle.add_argument("--created-at")
    bundle.add_argument("--pdf-page-format", choices=["A4", "Letter"], default="A4")
    bundle.add_argument("--audio-provider", choices=["macos", "openai"], default="macos")
    bundle.add_argument("--voice")
    bundle.add_argument("--rate", type=int, default=190)
    bundle.add_argument("--consent-network", action="store_true")
    bundle.add_argument("--json", action="store_true")

    verify = commands.add_parser("verify")
    verify.add_argument("target", type=Path)
    verify.add_argument(
        "--level",
        choices=[item.value for item in VerificationLevel],
        default=VerificationLevel.STRUCTURAL.value,
    )
    verify.add_argument("--workspace", type=Path)
    verify_network = verify.add_mutually_exclusive_group()
    verify_network.add_argument("--consent-network", action="store_true")
    verify_network.add_argument(
        "--offline",
        action="store_true",
        help="Compatibility alias for the default zero-network behavior",
    )
    verify_plugins = verify.add_mutually_exclusive_group()
    verify_plugins.add_argument("--allow-plugins", action="store_true")
    verify_plugins.add_argument(
        "--no-plugins",
        action="store_true",
        help="Make the default no-plugin verification policy explicit",
    )
    verify.add_argument("--allow-outside-workspace", action="store_true")
    verify.add_argument("--allow-large-artifact", action="store_true")
    verify.add_argument("--json", action="store_true")

    deliver = commands.add_parser("deliver")
    deliver.add_argument("bundle", type=Path)
    deliver.add_argument("--to", required=True, type=Path)
    deliver.add_argument("--force", action="store_true")
    deliver.add_argument("--allow-plugins", action="store_true")
    deliver.add_argument("--json", action="store_true")

    hook = commands.add_parser("hook", help=argparse.SUPPRESS)
    hook.add_argument(
        "--provider",
        required=True,
        choices=[item.value for item in Runtime] + ["auto"],
    )
    hook.add_argument("--event")
    hook.add_argument("--payload-json", help=argparse.SUPPRESS)
    hook.add_argument(
        "--output-profile",
        choices=["native", "vscode"],
        default="native",
    )

    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    show = config_commands.add_parser("show")
    show.add_argument("--json", action="store_true")
    init = config_commands.add_parser("init")
    init.add_argument("--scope", choices=["user", "project"], default="user")
    init.add_argument("--project", type=Path)
    init.add_argument("--force", action="store_true")

    state = commands.add_parser("state")
    state_commands = state.add_subparsers(dest="state_command", required=True)
    state_list = state_commands.add_parser("list")
    state_list.add_argument("--json", action="store_true")
    prune = state_commands.add_parser("prune")
    prune.add_argument("--older-than", type=int, metavar="DAYS")
    prune.add_argument("--dry-run", action="store_true")
    reset = state_commands.add_parser("reset")
    reset.add_argument("--runtime", required=True, choices=[item.value for item in Runtime])
    reset.add_argument("--session", required=True)

    return parser


def _force_utf8_output() -> None:
    """Briefs legitimately contain em dashes and arrows, but a Windows console
    defaults to cp1252 and raises UnicodeEncodeError while printing them. Ask the
    streams for UTF-8; ignore streams that cannot be reconfigured, such as a test
    harness capture object."""
    for stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError, OSError, ValueError):
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    if argv is None and Path(sys.argv[0]).name == "briefspec":
        print(
            "briefspec is a compatibility alias; prefer brief-spec before 1.0",
            file=sys.stderr,
        )
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"install", "uninstall"}:
            handler = install_runtime if args.command == "install" else uninstall_runtime
            runtimes = _runtimes(args.runtime)
            results = [
                handler(
                    runtime,
                    scope=args.scope,
                    project=args.project,
                    dry_run=args.dry_run,
                )
                for runtime in runtimes
            ]
            _print_result(results if len(results) > 1 else results[0], args.json)
            return 0

        if args.command == "setup":
            required = {
                Runtime(item.strip()) for item in (args.require or "").split(",") if item.strip()
            }
            detected = set(detected_harnesses())
            missing = sorted(runtime.value for runtime in required - detected)
            if missing:
                raise BriefSpecError(
                    "Required harness executable(s) are missing: " + ", ".join(missing)
                )
            runtimes = (
                [runtime for runtime in Runtime if runtime in detected]
                if args.runtime == "all"
                else _runtimes(args.runtime)
            )
            results = install_runtimes(
                runtimes,
                scope=args.scope,
                project=args.project,
                dry_run=args.dry_run,
            )
            if args.runtime == "all":
                for runtime in Runtime:
                    if runtime not in detected:
                        results.append(
                            {
                                "runtime": runtime.value,
                                "scope": args.scope,
                                "project": str(args.project) if args.project else None,
                                "dry_run": args.dry_run,
                                "operations": [],
                                "warnings": ["Harness executable not detected; setup skipped"],
                            }
                        )
            _print_result(
                results if len(results) != 1 else results[0],
                args.json,
            )
            return 0

        if args.command == "doctor":
            if args.replace_modified and not args.fix:
                raise ValueError("--replace-modified requires --fix")
            runtimes = _runtimes(args.runtime)
            if args.all_scopes:
                results = [
                    report
                    for runtime in runtimes
                    for report in doctor_all_scopes(
                        runtime,
                        project=args.project,
                        probe=args.probe,
                        optional_when_absent=args.runtime == "all",
                    )
                ]
            else:
                results = [
                    doctor_runtime(
                        runtime,
                        scope=args.scope,
                        project=args.project,
                        probe=args.probe,
                    )
                    for runtime in runtimes
                ]
            if args.fix:
                repairs = [
                    install_runtime(
                        Runtime(result["runtime"]),
                        scope=str(result["scope"]),
                        project=Path(result["project"]) if result.get("project") else None,
                        dry_run=args.dry_run,
                        replace_modified=args.replace_modified,
                    )
                    for result in results
                    if result["status"] == "FAIL"
                    or args.replace_modified
                    and any(
                        check["name"] == "managed file drift" for check in result.get("checks", [])
                    )
                ]
                if args.dry_run:
                    _print_result(
                        {
                            "host_repairs": repairs,
                            "renderer_repairs": setup_renderers(dry_run=True),
                        },
                        args.json,
                    )
                    return 0
                if repairs:
                    results = [
                        doctor_runtime(
                            Runtime(result["runtime"]),
                            scope=str(result["scope"]),
                            project=Path(result["project"]) if result.get("project") else None,
                            probe=args.probe,
                        )
                        for result in results
                    ]
                setup_renderers()
            _print_result(results if len(results) > 1 else results[0], args.json)
            return 1 if any(result["status"] == "FAIL" for result in results) else 0

        if args.command == "capabilities":
            results = [runtime_capabilities(runtime) for runtime in _runtimes(args.runtime)]
            rendered: Any = results if len(results) > 1 else results[0]
            if args.runtime == "all":
                rendered = {
                    "runtimes": results,
                    "renderers": renderer_capabilities(),
                    "contracts": {
                        "human_frame_request": "BriefSpecFrameRequest/v1",
                        "human_frame_receipt": "BriefSpecFrameReceipt/v1",
                    },
                    "authority": {
                        "approval": False,
                        "dispatch": False,
                    },
                }
            _print_result(rendered, args.json)
            return 0

        if args.command == "types":
            value = (
                types_document()
                if args.types_command == "list"
                else type_profile(args.type).to_dict()
            )
            _print_result(value, args.json)
            return 0

        if args.command == "classify":
            classification = classify_task(
                _read_text(args.input),
                explicit_type=args.type,
                subject=args.subject,
            )
            _print_result(classification.to_dict(), args.json)
            return 0

        if args.command == "frame":
            try:
                request = json.loads(_read_text(args.request))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Human Frame request is not JSON: {exc}") from exc
            receipt = render_frame(request, output=args.output, force=args.force)
            _print_result(receipt, args.json)
            return 0

        if args.command == "validate":
            if args.kind_or_path in {"auto", "outcome", "checkpoint"}:
                requested_kind = args.kind_or_path
                input_path = args.path or "-"
            else:
                if args.path is not None:
                    raise ValueError("validate accepts one input path unless a kind is specified")
                requested_kind = "auto"
                input_path = args.kind_or_path
            text = _read_text(input_path)
            if text.lstrip().startswith("{"):
                source_path = None if input_path == "-" else Path(input_path)
                delivery, load_warnings = load_delivery(text, source_path=source_path)
                result = validate_delivery(delivery)
                warnings = tuple(dict.fromkeys((*load_warnings, *result.warnings)))
                errors = (*result.errors, *(warnings if args.strict else ()))
                rendered = {
                    **result.to_dict(),
                    "valid": not errors,
                    "errors": list(errors),
                    "warnings": list(warnings),
                }
                _print_result(rendered, args.json)
                if not args.json:
                    print("VALID" if not errors else "INVALID")
                    for error in errors:
                        print(f"  ERROR: {error}")
                    for warning in warnings:
                        print(f"  WARN: {warning}")
                return 0 if not errors else 1
            kind = detect_kind(text) if requested_kind == "auto" else requested_kind
            if kind == "outcome":
                result = validate_outcome(text)
            elif kind == "checkpoint":
                mode = CheckpointMode(args.mode) if args.mode else None
                result = validate_checkpoint(text, mode)
            else:
                print("No Brief-Spec marker found", file=sys.stderr)
                return 1
            errors = (*result.errors, *(result.warnings if args.strict else ()))
            rendered = {
                **result.to_dict(),
                "valid": not errors,
                "errors": list(errors),
            }
            _print_result(rendered, args.json)
            if not args.json:
                print("VALID" if not errors else "INVALID")
                for error in errors:
                    print(f"  ERROR: {error}")
                for warning in result.warnings:
                    print(f"  WARN: {warning}")
            return 0 if not errors else 1

        if args.command in {"export", "bundle"}:
            text = _read_text(args.input)
            source_path = None if args.input == "-" else Path(args.input)
            delivery, warnings = load_delivery(
                text,
                source_path=source_path,
                runtime=args.runtime,
                harness=args.harness,
                session_ref=args.session_ref,
                host_version=args.host_version,
                adapter_version=args.adapter_version,
                source_revision=args.source_revision,
                model=args.model,
                model_provider=args.model_provider,
                created_at=args.created_at,
            )
            formats = [name.strip() for name in args.formats.split(",") if name.strip()]
            options = {
                "pdf": {"page_format": args.pdf_page_format},
                "audio": {
                    "provider": args.audio_provider,
                    "voice": args.voice,
                    "rate": args.rate,
                    "consent_network": args.consent_network,
                },
            }
            if args.command == "bundle":
                result = build_delivery_bundle(
                    delivery,
                    args.output,
                    formats=formats,
                    force=args.force,
                    renderer_options=options,
                )
            else:
                records = export_delivery_formats(
                    delivery,
                    formats,
                    args.output_dir,
                    force=args.force,
                    renderer_options=options,
                )
                result = {"outputs": records, "warnings": warnings}
            _print_result(result, args.json)
            return 0

        if args.command == "verify":
            result = verify_target(
                args.target,
                level=VerificationLevel(args.level),
                workspace=args.workspace,
                offline=args.offline,
                consent_network=args.consent_network,
                allow_plugins=args.allow_plugins,
                allow_outside_workspace=args.allow_outside_workspace,
                allow_large_artifact=args.allow_large_artifact,
            )
            _print_result(result, args.json)
            return 1 if result["status"] == "FAIL" else 0

        if args.command == "deliver":
            result = deliver_bundle(
                args.bundle,
                args.to,
                force=args.force,
                allow_plugins=args.allow_plugins,
            )
            _print_result(result, args.json)
            return 0

        if args.command == "hook":
            try:
                if args.payload_json is not None:
                    payload = json.loads(args.payload_json)
                    if not isinstance(payload, dict):
                        raise ValueError("hook payload must be a JSON object")
                else:
                    payload = read_hook_payload(sys.stdin)
                runtime = (
                    _detect_runtime(payload) if args.provider == "auto" else Runtime(args.provider)
                )
                event = normalize_event(runtime, payload, args.event)
                decision = process_event(event, payload)
                emit_diagnostics(decision)
                if runtime is Runtime.KIMI:
                    if decision.action == "block" and decision.reason:
                        print(decision.reason, file=sys.stderr)
                        return 2
                    if decision.context:
                        print(decision.context)
                    return 0
                print(
                    json.dumps(
                        render_decision(
                            runtime,
                            event.type,
                            decision,
                            output_profile=args.output_profile,
                        )
                    )
                )
            except Exception as exc:  # host hooks must fail open
                print(f"brief-spec: fail-open: {type(exc).__name__}: {exc}", file=sys.stderr)
                print("{}")
            return 0

        if args.command == "config":
            if args.config_command == "show":
                _print_result(load_config(), args.json)
                return 0
            destination = (
                ((args.project or Path.cwd()) / ".brief-spec.toml")
                if args.scope == "project"
                else briefspec_home() / "config.toml"
            )
            if destination.exists() and not args.force:
                print(f"Refusing to overwrite existing config: {destination}", file=sys.stderr)
                return 3
            atomic_write(destination, config_template().encode())
            print(destination)
            return 0

        if args.command == "state":
            if args.state_command == "list":
                _print_result(list_sessions(), args.json)
                return 0
            if args.state_command == "prune":
                days = args.older_than
                if days is None:
                    days = int(load_config()["state"]["retention_days"])
                removed = prune_sessions(days, args.dry_run)
                for path in removed:
                    print(path)
                return 0
            found = reset_session(Runtime(args.runtime), args.session)
            return 0 if found else 1
    except InstallConflict as exc:
        print(f"Installation conflict: {exc}", file=sys.stderr)
        return 3
    except (BriefSpecError, OSError, RuntimeError, ValueError) as exc:
        print(f"brief-spec: {exc}", file=sys.stderr)
        return 1
    return 2
