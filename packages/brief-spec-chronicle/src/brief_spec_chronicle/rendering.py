from __future__ import annotations

import html
import json
import os
import re
import stat
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from briefspec.artifacts import (
    artifact_record,
    atomic_write_set,
    build_manifest,
    sha256_bytes,
    verify_manifest,
)
from briefspec.verification import resolve_public_url

from brief_spec_chronicle import __version__
from brief_spec_chronicle.derive import validate_snapshot


def pretty_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"


def _list(items: list[Any], formatter: Any, empty: str = "None observed.") -> str:
    if not items:
        return f"- {empty}"
    return "\n".join(f"- {formatter(item)}" for item in items)


def render_markdown(snapshot: dict[str, Any]) -> str:
    errors = validate_snapshot(snapshot)
    if errors:
        raise ValueError("; ".join(errors))
    project = snapshot["project"]
    state = snapshot["current_state"]
    lines = [
        "<!-- brief-spec:chronicle:v1 -->",
        f"# {project['name']} — Project Chronicle",
        "",
        f"Window: {snapshot['window']['since']} → {snapshot['window']['until']}",
        f"Canonical SHA-256: `{snapshot['canonical_sha256']}`",
        "",
        "## 1. Intent in plain language",
        "",
        _list(
            snapshot["intent_anchors"],
            lambda item: f"{item['headline']} (`{item['event_id']}`)",
            "No explicit intent anchor was observed.",
        ),
        "",
        "## 2. Current phase and overall state",
        "",
        f"- Method: `{state['method']}`",
        f"- Phase: `{state['phase'] or 'unavailable'}`",
        f"- Current state: {state['headline']}",
        "",
        "## 3. Material changes",
        "",
        _list(
            snapshot["material_changes"],
            lambda item: (
                f"{item['occurred_at']} — **{item['kind']}**: {item['headline']}"
                + (" — late arrival" if item.get("late_arrival") else "")
            ),
        ),
        "",
        "## 4. Completed work with evidence",
        "",
        _list(
            snapshot["milestones"],
            lambda item: (
                f"**{item['kind']}**: {item['headline']} — evidence: "
                + (", ".join(f"`{value}`" for value in item["evidence_ids"]) or "unresolved")
            ),
        ),
        "",
        "## 5. Detours and drift",
        "",
        "### Detours",
        "",
        _list(
            snapshot["detours"],
            lambda item: f"{item['headline']} — {item.get('reason') or 'reason unavailable'}",
        ),
        "",
        "### Drift",
        "",
        _list(
            snapshot["drift"],
            lambda item: (
                f"**{item['severity']} / {item['category']}**: {item['observed']} "
                f"(expected: {item['expected']}; {item['disposition']})"
            ),
        ),
        "",
        "## 6. Decisions already made",
        "",
        _list(
            [item for item in snapshot["decisions"] if item["state"] == "recorded"],
            lambda item: (
                f"{item.get('question') or item['decision_id']} → **{item.get('choice')}**"
            ),
        ),
        "",
        "## 7. Decisions requiring human input",
        "",
        _list(
            [item for item in snapshot["decisions"] if item["state"] == "requested"],
            lambda item: str(item.get("question") or item["decision_id"]),
        ),
        "",
        "## 8. Risks, blockers, and unresolved evidence",
        "",
        _list(
            snapshot["blockers"],
            lambda item: (
                f"{item['headline']} — human action: {item.get('human_action') or 'unavailable'}"
            ),
        ),
        "",
        "## 9. Lessons and recurring patterns",
        "",
        _list(
            snapshot["lessons"],
            lambda item: f"{item['observation']} ({item['review_state']})",
        ),
        "",
        "## 10. Next three actions",
        "",
        _list(snapshot["next_actions"], str, "No explicit next action was observed."),
        "",
        "## 11. Evidence and provenance appendix",
        "",
        _list(
            snapshot["evidence"],
            lambda item: (
                f"`{item['evidence_id']}` — access: `{item['access']}`; expires: "
                f"`{item.get('expires_at') or 'not declared'}`"
            ),
        ),
        "",
        f"Ledger head: `{snapshot['ledger_head_hash']}`",
        "<!-- /brief-spec -->",
    ]
    return "\n".join(lines) + "\n"


def _html_list(items: list[Any], formatter: Any, empty: str = "None observed.") -> str:
    if not items:
        return f'<p class="empty">{html.escape(empty)}</p>'
    return "<ul>" + "".join(f"<li>{formatter(item)}</li>" for item in items) + "</ul>"


def render_html(snapshot: dict[str, Any]) -> str:
    errors = validate_snapshot(snapshot)
    if errors:
        raise ValueError("; ".join(errors))
    state = snapshot["current_state"]
    sections = [
        (
            "intent",
            "1. Intent in plain language",
            _html_list(
                snapshot["intent_anchors"],
                lambda item: html.escape(str(item["headline"])),
                "No explicit intent anchor was observed.",
            ),
        ),
        (
            "state",
            "2. Current phase and overall state",
            f"<dl><dt>Method</dt><dd>{html.escape(str(state['method']))}</dd>"
            f"<dt>Phase</dt><dd>{html.escape(str(state['phase'] or 'unavailable'))}</dd>"
            f"<dt>State</dt><dd>{html.escape(str(state['headline']))}</dd></dl>",
        ),
        (
            "changes",
            "3. Material changes",
            _html_list(
                snapshot["material_changes"],
                lambda item: (
                    f"<time>{html.escape(str(item['occurred_at']))}</time> — "
                    f"<strong>{html.escape(str(item['kind']))}</strong>: "
                    f"{html.escape(str(item['headline']))}"
                    + (" <em>(late arrival)</em>" if item.get("late_arrival") else "")
                ),
            ),
        ),
        (
            "milestones",
            "4. Completed work with evidence",
            _html_list(
                snapshot["milestones"],
                lambda item: (
                    f"<strong>{html.escape(str(item['kind']))}</strong>: "
                    f"{html.escape(str(item['headline']))} — evidence: "
                    f"{html.escape(', '.join(item['evidence_ids']) or 'unresolved')}"
                ),
            ),
        ),
        (
            "drift",
            "5. Detours and drift",
            "<h3>Detours</h3>"
            + _html_list(
                snapshot["detours"],
                lambda item: html.escape(
                    f"{item['headline']} — {item.get('reason') or 'reason unavailable'}"
                ),
            )
            + "<h3>Drift</h3>"
            + _html_list(
                snapshot["drift"],
                lambda item: html.escape(
                    f"{item['severity']} / {item['category']}: {item['observed']} "
                    f"(expected: {item['expected']}; {item['disposition']})"
                ),
            ),
        ),
        (
            "decided",
            "6. Decisions already made",
            _html_list(
                [item for item in snapshot["decisions"] if item["state"] == "recorded"],
                lambda item: html.escape(
                    f"{item.get('question') or item['decision_id']} → {item.get('choice')}"
                ),
            ),
        ),
        (
            "input",
            "7. Decisions requiring human input",
            _html_list(
                [item for item in snapshot["decisions"] if item["state"] == "requested"],
                lambda item: html.escape(str(item.get("question") or item["decision_id"])),
            ),
        ),
        (
            "risks",
            "8. Risks, blockers, and unresolved evidence",
            _html_list(
                snapshot["blockers"],
                lambda item: html.escape(
                    f"{item['headline']} — human action: "
                    f"{item.get('human_action') or 'unavailable'}"
                ),
            ),
        ),
        (
            "lessons",
            "9. Lessons and recurring patterns",
            _html_list(
                snapshot["lessons"],
                lambda item: html.escape(f"{item['observation']} ({item['review_state']})"),
            ),
        ),
        (
            "next",
            "10. Next three actions",
            _html_list(snapshot["next_actions"], lambda item: html.escape(str(item))),
        ),
        (
            "evidence",
            "11. Evidence and provenance appendix",
            _html_list(
                snapshot["evidence"],
                lambda item: (
                    f"<code>{html.escape(str(item['evidence_id']))}</code> — access: "
                    f"<strong>{html.escape(str(item['access']))}</strong>; expires: "
                    f"{html.escape(str(item.get('expires_at') or 'not declared'))}"
                ),
            ),
        ),
    ]
    body = "".join(
        f'<section id="{identifier}" aria-labelledby="{identifier}-title">'
        f'<h2 id="{identifier}-title">{title}</h2>{content}</section>'
        for identifier, title, content in sections
    )
    title = f"{snapshot['project']['name']} — Project Chronicle"
    canonical_hash = html.escape(str(snapshot["canonical_sha256"]))
    ledger_hash = html.escape(str(snapshot["ledger_head_hash"]))
    window_since = html.escape(str(snapshot["window"]["since"]))
    window_until = html.escape(str(snapshot["window"]["until"]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; base-uri 'none'; form-action 'none';
 style-src 'unsafe-inline'; img-src data:; font-src data:">
<meta name="brief-spec-canonical-sha256" content="{canonical_hash}">
<title>{html.escape(title)}</title><style>
:root {{ color-scheme: light; font-family: ui-sans-serif, system-ui, sans-serif;
 color: #18201d; background: #f4f1ea; }}
body {{ margin: 0; }} main {{ max-width: 60rem; margin: auto; padding: 2rem; }}
header {{ border-bottom: .25rem solid #1b5948; margin-bottom: 2rem; }}
h1 {{ font-size: clamp(2rem, 6vw, 4rem); line-height: .98; }} h2 {{ margin-top: 2rem; }}
section {{ background: #fff; border: 1px solid #d8d3c8; border-radius: .5rem;
 margin: 1rem 0; padding: 1rem 1.25rem; }}
li {{ margin: .45rem 0; overflow-wrap: anywhere; word-break: break-word; }}
dt {{ font-weight: 700; }} dd {{ margin: 0 0 .75rem; }}
code {{ overflow-wrap: anywhere; word-break: break-all; }} .meta, .empty {{ color: #58605c; }}
a:focus, summary:focus {{ outline: .2rem solid #a4512d; outline-offset: .2rem; }}
@media print {{ :root {{ background: #fff; }} main {{ max-width: none; padding: 0; }}
 section {{ break-inside: avoid; }} }}
</style></head><body><main><header><p>Brief-Spec Human Review Pack</p>
<h1>{html.escape(title)}</h1><p class="meta">{window_since} → {window_until}</p>
<p class="meta">Canonical SHA-256: <code>{canonical_hash}</code></p></header>{body}
<footer><p class="meta">Ledger head: <code>{ledger_hash}</code></p></footer>
</main></body></html>
"""


def render_spoken_text(snapshot: dict[str, Any]) -> str:
    state = snapshot["current_state"]
    sentences = [
        f"Project Chronicle for {snapshot['project']['name']}.",
        f"The current method is {state['method']}.",
        f"The current phase is {state['phase'] or 'unavailable'}.",
        f"Current state: {state['headline']}.",
    ]
    if snapshot["milestones"]:
        sentences.append(
            "Completed milestones: "
            + "; ".join(item["headline"] for item in snapshot["milestones"])
        )
    open_drift = [item for item in snapshot["drift"] if item["disposition"] == "open"]
    if open_drift:
        sentences.append("Open drift: " + "; ".join(item["observed"] for item in open_drift))
    requested = [item for item in snapshot["decisions"] if item["state"] == "requested"]
    if requested:
        sentences.append(
            "Human decisions required: "
            + "; ".join(str(item.get("question") or item["decision_id"]) for item in requested)
        )
    if snapshot["next_actions"]:
        sentences.append("Next actions: " + "; ".join(snapshot["next_actions"]))
    return "\n\n".join(sentences) + "\n"


def _zip_datetime(value: str) -> tuple[int, int, int, int, int, int]:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        max(timestamp.year, 1980),
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second,
    )


def deterministic_zip(files: dict[str, bytes], created_at: str) -> bytes:
    descriptor, temporary = tempfile.mkstemp(prefix="brief-spec-chronicle-", suffix=".zip")
    temp_path = Path(temporary)
    try:
        os.close(descriptor)
        with zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=_zip_datetime(created_at))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, files[name])
        return temp_path.read_bytes()
    finally:
        temp_path.unlink(missing_ok=True)


def export_snapshot(
    snapshot: dict[str, Any],
    output_dir: Path,
    *,
    formats: set[str],
    force: bool = False,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors = validate_snapshot(snapshot)
    if errors:
        raise ValueError("; ".join(errors))
    allowed = {
        "markdown",
        "json",
        "html",
        "zip",
        "spoken-text",
        "pdf",
        "audio",
        "video",
    }
    unknown = formats - allowed
    if unknown:
        raise ValueError("Unknown Chronicle format(s): " + ", ".join(sorted(unknown)))
    if not formats:
        raise ValueError("At least one Chronicle format is required")
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"Chronicle output directory is not a directory: {output_dir}")
    rendered: dict[str, bytes] = {}
    if "markdown" in formats or "zip" in formats:
        rendered["chronicle.md"] = render_markdown(snapshot).encode()
    # Every presentation carries its canonical anchor for independent semantic verification.
    rendered["chronicle.json"] = pretty_json_bytes(snapshot)
    if "html" in formats or "zip" in formats or "pdf" in formats:
        rendered["chronicle.html"] = render_html(snapshot).encode()
    if "spoken-text" in formats or "audio" in formats or "zip" in formats:
        rendered["chronicle-spoken.txt"] = render_spoken_text(snapshot).encode()
    included: dict[str, bytes] = {}
    media = {
        ".md": "text/markdown",
        ".json": "application/json",
        ".html": "text/html",
        ".txt": "text/plain",
    }
    for name, content in rendered.items():
        if name == "chronicle.html" and "html" not in formats and "zip" not in formats:
            continue
        if name == "chronicle-spoken.txt" and "spoken-text" not in formats and "zip" not in formats:
            continue
        if name == "chronicle.md" and "markdown" not in formats and "zip" not in formats:
            continue
        included[name] = content
    options = options or {}
    output_names = set(included) | {"manifest.json", "chronicle-receipt.json"}
    if "pdf" in formats:
        output_names.add("chronicle.pdf")
    if "audio" in formats:
        output_names.add("chronicle.mp3")
    if "video" in formats:
        output_names.update(
            {"chronicle.mp4", "chronicle.storyboard.json", "chronicle.vtt", "chronicle.txt"}
        )
    if "zip" in formats:
        output_names.add("chronicle.zip")
    conflicts = [output_dir / name for name in sorted(output_names) if (output_dir / name).exists()]
    if conflicts and not force:
        raise FileExistsError(f"Refusing to overwrite existing output: {conflicts[0]}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="brief-spec-chronicle-export-") as temporary:
        stage = Path(temporary)
        records: list[dict[str, Any]] = []
        for name, content in included.items():
            path = stage / name
            path.write_bytes(content)
            records.append(
                artifact_record(
                    format_name=path.suffix.removeprefix("."),
                    filename=name,
                    media_type=media[path.suffix],
                    content=content,
                    renderer_version=__version__,
                )
            )
        if "pdf" in formats:
            try:
                from briefspec_renderer_pdf import render_html_document
            except ImportError as exc:
                raise ValueError("PDF output requires brief-spec-renderer-pdf") from exc
            records.append(
                render_html_document(
                    rendered["chronicle.html"],
                    stage / "chronicle.pdf",
                    created_at=snapshot["window"]["created_at"],
                    title=f"{snapshot['project']['name']} — Project Chronicle",
                    page_format=str(options.get("page_format", "A4")),
                )
            )
        if "audio" in formats:
            try:
                from briefspec_renderer_audio import render_script_document
            except ImportError as exc:
                raise ValueError("Audio output requires brief-spec-renderer-audio") from exc
            records.append(
                render_script_document(
                    rendered["chronicle-spoken.txt"].decode(),
                    stage / "chronicle.mp3",
                    created_at=snapshot["window"]["created_at"],
                    provider=str(options.get("provider", "macos")),
                    voice=options.get("voice"),
                    rate=int(options.get("rate", 190)),
                    consent_network=bool(options.get("consent_network")),
                )
            )
        if "video" in formats:
            try:
                from brief_spec_renderer_video import render_chronicle_video
            except ImportError as exc:
                raise ValueError("Video output requires brief-spec-renderer-video") from exc
            record = render_chronicle_video(
                snapshot,
                stage / "chronicle.mp4",
                provider=str(options.get("provider", "macos")),
                voice=options.get("voice"),
                rate=int(options.get("rate", 190)),
                consent_network=bool(options.get("consent_network")),
            )
            records.append(record)
            sidecar_media = {
                ".json": "application/json",
                ".vtt": "text/vtt",
                ".txt": "text/plain",
            }
            for sidecar in record.get("metadata", {}).get("sidecars", []):
                path = stage / str(sidecar["path"])
                records.append(
                    artifact_record(
                        format_name=f"video-{path.suffix.removeprefix('.')}",
                        filename=path.name,
                        media_type=sidecar_media[path.suffix],
                        content=path.read_bytes(),
                        renderer_version=str(record["renderer_version"]),
                    )
                )
        manifest = build_manifest(
            kind="brief-spec-chronicle-manifest",
            schema_version="brief-spec-chronicle-manifest/1.0",
            canonical_sha256=snapshot["canonical_sha256"],
            created_at=snapshot["window"]["created_at"],
            files=records,
            metadata={"ledger_head_hash": snapshot["ledger_head_hash"]},
        )
        manifest_content = pretty_json_bytes(manifest)
        (stage / "manifest.json").write_bytes(manifest_content)
        receipt_artifacts = [
            *records,
            artifact_record(
                format_name="manifest",
                filename="manifest.json",
                media_type="application/json",
                content=manifest_content,
                renderer_version=__version__,
            ),
        ]
        if "zip" in formats:
            zip_files = {
                record["path"]: (stage / str(record["path"])).read_bytes() for record in records
            }
            zip_files["manifest.json"] = manifest_content
            bundle = deterministic_zip(zip_files, snapshot["window"]["created_at"])
            (stage / "chronicle.zip").write_bytes(bundle)
            receipt_artifacts.append(
                artifact_record(
                    format_name="zip",
                    filename="chronicle.zip",
                    media_type="application/zip",
                    content=bundle,
                    renderer_version=__version__,
                )
            )
        receipt = {
            "schema_version": "brief-spec-chronicle-receipt/1.0",
            "project_id": snapshot["project"]["project_id"],
            "ledger_head_hash": snapshot["ledger_head_hash"],
            "snapshot_sha256": snapshot["canonical_sha256"],
            "created_at": snapshot["window"]["created_at"],
            "destination": {
                "kind": "local-directory",
                "locator": str(output_dir.expanduser().absolute()),
                "access": "local",
            },
            "artifacts": sorted(receipt_artifacts, key=lambda item: str(item["path"])),
        }
        receipt_content = pretty_json_bytes(receipt)
        (stage / "chronicle-receipt.json").write_bytes(receipt_content)
        writes = [
            (output_dir / name, (stage / name).read_bytes(), 0o644) for name in sorted(output_names)
        ]
        atomic_write_set(writes)
    return {
        "snapshot_sha256": snapshot["canonical_sha256"],
        "files": records,
        "manifest": manifest,
        "receipt": str(output_dir / "chronicle-receipt.json"),
    }


def _verify_html_content(content: str, snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if snapshot["canonical_sha256"] not in content:
        errors.append("HTML canonical hash is missing")
    for landmark in ("<main>", "<header>", "<section", "<footer>"):
        if landmark not in content:
            errors.append(f"HTML landmark is missing: {landmark}")
    for directive in ("default-src 'none'", "base-uri 'none'", "form-action 'none'"):
        if directive not in content:
            errors.append(f"HTML CSP directive is missing: {directive}")
    if re.search(r'<(?:script|link)\b[^>]*(?:src|href)=["\']https?://', content, re.I):
        errors.append("HTML contains an external script or stylesheet")
    return errors


def _verify_receipt(receipt: dict[str, Any], root: Path, snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipt.get("schema_version") != "brief-spec-chronicle-receipt/1.0":
        errors.append("Chronicle receipt schema is invalid")
    for field, expected in (
        ("project_id", snapshot["project"]["project_id"]),
        ("ledger_head_hash", snapshot["ledger_head_hash"]),
        ("snapshot_sha256", snapshot["canonical_sha256"]),
    ):
        if receipt.get(field) != expected:
            errors.append(f"Chronicle receipt {field} does not match the snapshot")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return [*errors, "Chronicle receipt artifacts must be a non-empty array"]
    for index, record in enumerate(artifacts):
        if not isinstance(record, dict):
            errors.append(f"Chronicle receipt artifact {index} is invalid")
            continue
        name = str(record.get("path", ""))
        if not name or Path(name).name != name or name == "chronicle-receipt.json":
            errors.append(f"Chronicle receipt artifact path is unsafe: {name}")
            continue
        path = root / name
        if not path.is_file():
            errors.append(f"Chronicle receipt artifact is missing: {name}")
            continue
        content = path.read_bytes()
        if record.get("size_bytes") != len(content):
            errors.append(f"Chronicle receipt size mismatch: {name}")
        if record.get("sha256") != sha256_bytes(content):
            errors.append(f"Chronicle receipt hash mismatch: {name}")
    return errors


def _resolve_evidence(
    snapshot: dict[str, Any], workspace: Path, *, offline: bool
) -> tuple[list[str], list[str], list[str]]:
    checks: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    root = workspace.expanduser().resolve()
    try:
        created_at = datetime.fromisoformat(
            str(snapshot["window"]["created_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        return checks, ["Snapshot creation time is invalid; evidence cannot be resolved"], warnings
    if created_at.tzinfo is None:
        return checks, ["Snapshot creation time has no timezone"], warnings
    records = snapshot.get("evidence") or [
        {
            "evidence_id": locator,
            "access": "local",
            "expires_at": None,
            "content_sha256": None,
        }
        for locator in snapshot.get("evidence_ids", [])
    ]
    for record in records:
        if not isinstance(record, dict) or not record.get("evidence_id"):
            errors.append("Evidence record is invalid")
            continue
        locator = str(record["evidence_id"])
        expires_at = record.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"Evidence expiry is invalid: {locator}")
                continue
            if expiry.tzinfo is None:
                errors.append(f"Evidence expiry has no timezone: {locator}")
                continue
            if expiry <= created_at:
                errors.append(f"Evidence was expired at snapshot creation: {locator}")
                continue
        if locator.startswith("file:"):
            raw = locator[5:]
            expected = record.get("content_sha256")
            if "#sha256=" in raw:
                raw, expected = raw.rsplit("#sha256=", 1)
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError:
                errors.append(f"Evidence path escapes workspace: {locator}")
                continue
            if not resolved.is_file():
                errors.append(f"Evidence file is missing: {locator}")
                continue
            content = resolved.read_bytes()
            if len(content) > 256 * 1024 * 1024:
                errors.append(f"Evidence file exceeds 256 MiB: {locator}")
                continue
            actual = sha256_bytes(content)
            if expected and expected != actual:
                errors.append(f"Evidence file hash mismatch: {locator}")
            else:
                checks.append(f"Evidence file resolved: {locator}")
        elif locator.startswith("commit:"):
            revision = locator[7:]
            result = subprocess.run(
                ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
                cwd=root,
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode:
                errors.append(f"Git commit is missing: {revision}")
            else:
                checks.append(f"Git commit resolved: {revision}")
        elif locator.startswith(("https://", "http://")):
            if record.get("access") == "private":
                warnings.append(f"Private evidence URL requires authorized review: {locator}")
                continue
            if offline:
                warnings.append(f"Offline mode left URL unresolved: {locator}")
                continue
            try:
                result = resolve_public_url(
                    locator,
                    expected_sha256=(
                        str(record["content_sha256"]) if record.get("content_sha256") else None
                    ),
                )
            except (OSError, ValueError) as exc:
                errors.append(f"Evidence URL could not be resolved: {exc}")
            else:
                if record.get("content_sha256") and result["sha256"] != record["content_sha256"]:
                    errors.append(f"Evidence URL content hash mismatch: {locator}")
                    continue
                checks.append(
                    f"Evidence URL resolved with HTTP {result['status']}: {result['final_url']}"
                )
        else:
            warnings.append(f"Opaque evidence ID is not a resolvable locator: {locator}")
    return checks, errors, warnings


def _verify_renderers(root: Path, manifest: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    records = {str(record.get("path")): record for record in manifest.get("files", [])}
    html_path = root / "chronicle.html"
    if html_path.is_file():
        errors.extend(_verify_html_content(html_path.read_text(encoding="utf-8"), snapshot))
    json_path = root / "chronicle.json"
    if json_path.is_file() and json.loads(json_path.read_text(encoding="utf-8")) != snapshot:
        errors.append("Rendered Chronicle JSON is not the canonical snapshot")
    markdown = root / "chronicle.md"
    if markdown.is_file() and snapshot["canonical_sha256"] not in markdown.read_text(
        encoding="utf-8"
    ):
        errors.append("Markdown canonical hash is missing")
    pdf = root / "chronicle.pdf"
    if pdf.is_file():
        try:
            from briefspec_renderer_pdf import PDFRenderer
        except ImportError:
            errors.append("PDF renderer is required to verify chronicle.pdf")
        else:
            result = PDFRenderer().verify(pdf)
            if result.get("status") != "PASS":
                errors.append(f"PDF verification failed: {result.get('detail')}")
            expected = sha256_bytes(render_html(snapshot).encode())
            if (
                records.get("chronicle.pdf", {}).get("metadata", {}).get("source_html_sha256")
                != expected
            ):
                errors.append("PDF source HTML hash does not match the snapshot")
    audio = root / "chronicle.mp3"
    if audio.is_file():
        try:
            from briefspec_renderer_audio import AudioRenderer
        except ImportError:
            errors.append("Audio renderer is required to verify chronicle.mp3")
        else:
            result = AudioRenderer().verify(audio)
            if result.get("status") != "PASS":
                errors.append(f"Audio verification failed: {result.get('detail')}")
            expected = sha256_bytes(render_spoken_text(snapshot).encode())
            if (
                records.get("chronicle.mp3", {}).get("metadata", {}).get("source_script_sha256")
                != expected
            ):
                errors.append("Audio source script hash does not match the snapshot")
    video = root / "chronicle.mp4"
    if video.is_file():
        try:
            from brief_spec_renderer_video import verify_video
        except ImportError:
            errors.append("Video renderer is required to verify chronicle.mp4")
        else:
            result = verify_video(video)
            if result.get("status") != "PASS":
                errors.append(f"Video verification failed: {result.get('detail')}")
            if (
                records.get("chronicle.mp4", {}).get("metadata", {}).get("source_snapshot_sha256")
                != snapshot["canonical_sha256"]
            ):
                errors.append("Video source snapshot hash does not match the snapshot")
    return errors


def _verify_directory(
    root: Path, *, level: str, workspace: Path, offline: bool
) -> tuple[list[str], list[str], list[str]]:
    checks: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = root / "manifest.json"
    snapshot_path = root / "chronicle.json"
    receipt_path = root / "chronicle-receipt.json"
    if not manifest_path.is_file():
        return checks, ["manifest.json is missing"], warnings
    if not snapshot_path.is_file():
        return checks, ["chronicle.json is missing"], warnings
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return checks, [f"Chronicle export JSON is invalid: {exc}"], warnings
    errors.extend(verify_manifest(manifest, root))
    errors.extend(validate_snapshot(snapshot))
    if manifest.get("canonical_sha256") != snapshot.get("canonical_sha256"):
        errors.append("Manifest canonical hash does not match the snapshot")
    checks.extend(["manifest files and hashes", "canonical Chronicle hash"])
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        errors.extend(_verify_receipt(receipt, root, snapshot))
        checks.append("external Chronicle receipt")
    else:
        errors.append("chronicle-receipt.json is missing")
    if level in {"resolved", "rendered"}:
        resolved_checks, resolved_errors, resolved_warnings = _resolve_evidence(
            snapshot, workspace, offline=offline
        )
        checks.extend(resolved_checks)
        errors.extend(resolved_errors)
        warnings.extend(resolved_warnings)
    if level == "rendered":
        errors.extend(_verify_renderers(root, manifest, snapshot))
        checks.append("renderer semantics and source hashes")
    return checks, errors, warnings


def _verify_zip(
    target: Path, *, level: str, workspace: Path, offline: bool
) -> tuple[list[str], list[str], list[str]]:
    checks: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    if target.stat().st_size > 64 * 1024 * 1024:
        return checks, ["Chronicle ZIP exceeds 64 MiB"], warnings
    try:
        with zipfile.ZipFile(target) as archive:
            members = archive.infolist()
            if len(members) > 256:
                raise ValueError("Chronicle ZIP exceeds 256 members")
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise ValueError("Chronicle ZIP contains duplicate members")
            total = 0
            for member in members:
                path = Path(member.filename)
                mode = member.external_attr >> 16
                allowed_nested = len(path.parts) == 2 and path.parts[0] in {
                    "events",
                    "receipts",
                }
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in member.filename
                    or not (len(path.parts) == 1 or allowed_nested)
                    or stat.S_ISLNK(mode)
                ):
                    raise ValueError(f"Unsafe Chronicle ZIP member: {member.filename}")
                total += member.file_size
                if member.file_size > 64 * 1024 * 1024 or total > 256 * 1024 * 1024:
                    raise ValueError("Chronicle ZIP exceeds expanded-size limits")
                if member.file_size and (
                    member.compress_size == 0 or member.file_size / member.compress_size > 100
                ):
                    raise ValueError(
                        f"Chronicle ZIP member has unsafe compression: {member.filename}"
                    )
            if "manifest.json" not in names or "chronicle.json" not in names:
                raise ValueError("Chronicle ZIP is missing manifest.json or chronicle.json")
            manifest = json.loads(archive.read("manifest.json"))
            archive_schema = manifest.get("schema_version") == "brief-spec-chronicle-archive/1.0"
            if not archive_schema and any(len(Path(name).parts) != 1 for name in names):
                raise ValueError("Portable delivery ZIP contains nested member paths")
            expected = sorted(str(item["path"]) for item in manifest.get("files", []))
            actual = sorted(name for name in names if name != "manifest.json")
            if expected != actual:
                raise ValueError("Chronicle ZIP members do not match its manifest")
            for record in manifest["files"]:
                content = archive.read(record["path"])
                if record.get("size_bytes") != len(content):
                    raise ValueError(f"Chronicle ZIP size mismatch: {record['path']}")
                if record.get("sha256") != sha256_bytes(content):
                    raise ValueError(f"Chronicle ZIP hash mismatch: {record['path']}")
            with tempfile.TemporaryDirectory(prefix="brief-spec-chronicle-verify-") as temporary:
                root = Path(temporary)
                for name in names:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(name))
                snapshot = json.loads((root / "chronicle.json").read_text(encoding="utf-8"))
                errors.extend(validate_snapshot(snapshot))
                if archive_schema:
                    from briefspec.events import validate_event

                    from brief_spec_chronicle.storage import order_events, verify_chain

                    events: list[dict[str, Any]] = []
                    for name in sorted(item for item in names if item.startswith("events/")):
                        for line_number, line in enumerate(
                            (root / name).read_text(encoding="utf-8").splitlines(), start=1
                        ):
                            event = json.loads(line)
                            event_errors = validate_event(event)
                            if event_errors:
                                raise ValueError(
                                    f"Invalid archived event {name}:{line_number}: "
                                    + "; ".join(event_errors)
                                )
                            if event.get("project_id") != snapshot.get("project", {}).get(
                                "project_id"
                            ):
                                raise ValueError(
                                    f"Archived event project ID mismatch: {name}:{line_number}"
                                )
                            events.append(event)
                    events = order_events(events)
                    chain_errors = verify_chain(events)
                    if chain_errors:
                        errors.extend(chain_errors)
                    head = events[-1]["event_hash"] if events else "0" * 64
                    if manifest.get("ledger_head_hash") != head:
                        errors.append("Chronicle archive ledger head does not match its events")
                    if snapshot.get("ledger_head_hash") != head:
                        errors.append("Chronicle archive snapshot ledger head does not match")
                    checks.append("archive event schemas and hash chain")
                elif manifest.get("canonical_sha256") != snapshot.get("canonical_sha256"):
                    errors.append("Chronicle ZIP canonical hash does not match its manifest")
                if level in {"resolved", "rendered"}:
                    found, failed, unresolved = _resolve_evidence(
                        snapshot, workspace, offline=offline
                    )
                    checks.extend(found)
                    errors.extend(failed)
                    warnings.extend(unresolved)
                if level == "rendered" and not archive_schema:
                    errors.extend(_verify_renderers(root, manifest, snapshot))
    except (OSError, KeyError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        errors.append(str(exc))
    checks.append("ZIP members, manifest, and hashes")
    return checks, errors, warnings


def verify_export(
    target: Path,
    *,
    level: str = "structural",
    workspace: Path | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    if level not in {"structural", "resolved", "rendered"}:
        raise ValueError(f"Unknown Chronicle verification level: {level}")
    target = target.expanduser().resolve()
    root = (workspace or Path.cwd()).expanduser().resolve()
    checks: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    if target.is_file() and target.suffix.lower() == ".zip":
        checks, errors, warnings = _verify_zip(target, level=level, workspace=root, offline=offline)
    elif target.is_file() and target.name in {"manifest.json", "chronicle-receipt.json"}:
        checks, errors, warnings = _verify_directory(
            target.parent, level=level, workspace=root, offline=offline
        )
    elif (
        target.is_file()
        and target.suffix.lower() == ".json"
        and not target.name.endswith(".storyboard.json")
    ):
        try:
            snapshot = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"Chronicle JSON is invalid: {exc}")
        else:
            errors.extend(validate_snapshot(snapshot))
            checks.append("canonical Chronicle structure")
            if level in {"resolved", "rendered"}:
                found, failed, unresolved = _resolve_evidence(snapshot, root, offline=offline)
                checks.extend(found)
                errors.extend(failed)
                warnings.extend(unresolved)
            if level == "rendered":
                warnings.append("A canonical JSON snapshot has no rendered artifacts to inspect")
    elif target.is_file() and target.suffix.lower() in {
        ".md",
        ".html",
        ".pdf",
        ".mp3",
        ".mp4",
        ".txt",
        ".vtt",
        ".json",
    }:
        manifest_path = target.parent / "manifest.json"
        snapshot_path = target.parent / "chronicle.json"
        if manifest_path.is_file() and snapshot_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"Adjacent Chronicle manifest is invalid: {exc}")
            else:
                declared = {str(item.get("path")) for item in manifest.get("files", [])}
                if target.name not in declared:
                    errors.append(
                        f"Artifact is not declared by the adjacent manifest: {target.name}"
                    )
                found, failed, unresolved = _verify_directory(
                    target.parent, level=level, workspace=root, offline=offline
                )
                checks.extend(found)
                errors.extend(failed)
                warnings.extend(unresolved)
        else:
            errors.append(
                "Standalone artifact lacks adjacent chronicle.json and manifest.json anchors"
            )
    elif target.is_dir():
        checks, errors, warnings = _verify_directory(
            target, level=level, workspace=root, offline=offline
        )
    else:
        errors.append(
            "Target must be a Chronicle artifact, JSON, ZIP, receipt, or export directory"
        )
    status = "FAIL" if errors else ("WARN" if warnings else "PASS")
    return {
        "status": status,
        "level": level,
        "target": str(target),
        "workspace": str(root),
        "offline": offline,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }
