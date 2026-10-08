from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec.delivery import (
    CORE_FORMATS,
    CORE_RENDERER_VERSION,
    canonical_sha256,
    export_core,
    sha256_bytes,
)
from briefspec.renderers import render_with_plugin
from briefspec.state import atomic_write, atomic_write_many

_SKIP_PARTS = {"__pycache__", "resources"}


def build_zipapp(destination: Path) -> str:
    """Build a deterministic, stdlib-only Brief-Spec zipapp."""
    package_root = Path(__file__).resolve().parent
    files: list[tuple[Path, str]] = []
    for path in package_root.rglob("*"):
        if not path.is_file() or any(part in _SKIP_PARTS for part in path.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        if path.suffix == ".jsonl" and path.name.startswith("classification-"):
            continue  # evaluation corpora are for `brief-spec eval`, not for host hooks
        relative = path.relative_to(package_root)
        files.append((path, f"briefspec/{relative.as_posix()}"))

    main = b"from briefspec.cli import main\nraise SystemExit(main())\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=".briefspec.",
        suffix=".pyz",
        dir=destination.parent,
    )
    os.close(descriptor)
    temp = Path(raw_temp)
    try:
        with zipfile.ZipFile(
            temp,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            main_info = zipfile.ZipInfo("__main__.py", date_time=(1980, 1, 1, 0, 0, 0))
            main_info.external_attr = 0o644 << 16
            archive.writestr(main_info, main)
            for source, archive_name in sorted(files, key=lambda item: item[1]):
                info = zipfile.ZipInfo(archive_name, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                archive.writestr(info, source.read_bytes())
        payload = temp.read_bytes()
        atomic_write(destination, payload, mode=0o700)
    finally:
        temp.unlink(missing_ok=True)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def _zip_time(delivery: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    raw = str(delivery.get("source", {}).get("created_at", "1980-01-01T00:00:00Z"))
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        stamp = datetime(1980, 1, 1, tzinfo=UTC)
    if stamp.year < 1980:
        stamp = datetime(1980, 1, 1, tzinfo=UTC)
    if stamp.year > 2107:
        stamp = datetime(2107, 12, 31, 23, 59, 58, tzinfo=UTC)
    return stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second // 2 * 2


def _record_for(
    path: Path,
    *,
    output_format: str,
    media_type: str,
    renderer: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = path.read_bytes()
    record = {
        "format": output_format,
        "path": path.name,
        "media_type": media_type,
        "size_bytes": len(content),
        "sha256": sha256_bytes(content),
        "renderer_version": renderer,
    }
    if metadata:
        record["metadata"] = metadata
    return record


def export_delivery_formats(
    delivery: dict[str, Any],
    formats: list[str],
    output_dir: Path,
    *,
    force: bool = False,
    renderer_options: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Render every selected format before atomically committing the output set."""
    if len(formats) != len(set(formats)):
        raise ValueError("Duplicate output formats are not allowed")
    renderer_options = renderer_options or {}
    with tempfile.TemporaryDirectory(prefix="briefspec-export-") as temporary:
        root = Path(temporary)
        core = [name for name in formats if name in CORE_FORMATS]
        plugins = [name for name in formats if name not in CORE_FORMATS]
        records = export_core(delivery, core, root)
        for name in plugins:
            records.append(
                render_with_plugin(
                    name,
                    delivery,
                    root,
                    force=True,
                    options=renderer_options.get(name, {}),
                )
            )
        filenames = [Path(record["path"]).name for record in records]
        if len(filenames) != len(set(filenames)):
            raise ValueError("Selected renderers produce duplicate output filenames")
        targets = [output_dir / filename for filename in filenames]
        conflicts = [path for path in targets if path.exists() and not force]
        if conflicts:
            raise FileExistsError(f"Refusing to overwrite existing output: {conflicts[0]}")
        atomic_write_many(
            [
                (target, Path(record["path"]).read_bytes(), 0o644)
                for target, record in zip(targets, records, strict=True)
            ]
        )
        return [
            {**record, "path": str(target)} for target, record in zip(targets, records, strict=True)
        ]


def build_delivery_bundle(
    delivery: dict[str, Any],
    destination: Path,
    *,
    formats: list[str] | None = None,
    force: bool = False,
    renderer_options: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one deterministic bundle and a non-attesting receipt template."""
    if destination.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing output: {destination}")
    receipt_template = destination.with_suffix(destination.suffix + ".receipt.template.json")
    if receipt_template.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing output: {receipt_template}")
    requested = formats or []
    if len(requested) != len(set(requested)):
        raise ValueError("Duplicate output formats are not allowed")
    required = ["markdown", "json", "html"]
    selected = [*required, *(name for name in requested if name not in required)]
    renderer_options = renderer_options or {}
    with tempfile.TemporaryDirectory(prefix="briefspec-delivery-") as temporary:
        root = Path(temporary)
        core = [name for name in selected if name in CORE_FORMATS]
        plugin_names = [name for name in selected if name not in CORE_FORMATS]
        records = export_core(delivery, core, root)
        for name in plugin_names:
            records.append(
                render_with_plugin(
                    name,
                    delivery,
                    root,
                    force=True,
                    options=renderer_options.get(name, {}),
                )
            )
        normalized = []
        for record in records:
            path = Path(record["path"])
            normalized.append(
                _record_for(
                    path,
                    output_format=str(record.get("format", path.suffix.lstrip("."))),
                    media_type=str(record.get("media_type", "application/octet-stream")),
                    renderer=str(record.get("renderer_version", CORE_RENDERER_VERSION)),
                    metadata=(
                        record.get("metadata") if isinstance(record.get("metadata"), dict) else None
                    ),
                )
            )
        manifest = {
            "schema_version": "2.0",
            "kind": "brief-spec-bundle-manifest",
            "brief_spec_version": delivery["source"]["brief_spec_version"],
            "delivery_schema_version": delivery["schema_version"],
            "canonical_sha256": canonical_sha256(delivery),
            "created_at": delivery["source"]["created_at"],
            "files": sorted(normalized, key=lambda item: item["path"]),
        }
        manifest_bytes = (
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )
        entries = [(root / item["path"], item["path"]) for item in manifest["files"]]
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temp = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
        temp = Path(raw_temp)
        try:
            with zipfile.ZipFile(
                temp,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                for path, archive_name in sorted(entries, key=lambda item: item[1]):
                    info = zipfile.ZipInfo(archive_name, date_time=_zip_time(delivery))
                    info.external_attr = 0o644 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())
                info = zipfile.ZipInfo("manifest.json", date_time=_zip_time(delivery))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, manifest_bytes)
            bundle_content = temp.read_bytes()
        finally:
            temp.unlink(missing_ok=True)
    bundle_hash = hashlib.sha256(bundle_content).hexdigest()
    template = {
        "schema_version": "2.0",
        "kind": "brief-spec-delivery-receipt",
        "status": "pending",
        "delivery_id": None,
        "format": "application/zip",
        "destination": {"kind": "local", "locator": None},
        "content_sha256": bundle_hash,
        "brief_spec_version": delivery["source"]["brief_spec_version"],
        "delivery_schema_version": delivery["schema_version"],
        "renderer_versions": sorted({str(item["renderer_version"]) for item in manifest["files"]}),
        "verification_level": "rendered",
        "delivered_at": None,
    }
    template_content = json.dumps(template, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write_many(
        [
            (destination, bundle_content, 0o644),
            (receipt_template, template_content, 0o644),
        ]
    )
    return {
        "bundle": str(destination),
        "receipt_template": str(receipt_template),
        "sha256": bundle_hash,
        "manifest": manifest,
    }


def deliver_bundle(
    bundle: Path,
    destination: Path,
    *,
    force: bool = False,
    allow_plugins: bool = False,
) -> dict[str, Any]:
    if not bundle.is_file():
        raise FileNotFoundError(f"Bundle does not exist: {bundle}")
    # Delivery is a stronger claim than copying bytes. Refuse to emit a delivered
    # receipt for a bundle that cannot first prove its rendered integrity.
    from briefspec.verification import verify_bundle_integrity

    verification = verify_bundle_integrity(bundle, allow_plugins=allow_plugins)
    if verification["status"] != "PASS":
        failures = [
            str(check["detail"]) for check in verification["checks"] if check["status"] == "FAIL"
        ]
        detail = failures[0] if failures else "rendered verification did not pass"
        raise ValueError(f"Refusing to deliver an invalid bundle: {detail}")
    target = (
        destination / bundle.name if destination.is_dir() or not destination.suffix else destination
    )
    receipt_path = target.with_suffix(target.suffix + ".receipt.json")
    conflicts = [path for path in (target, receipt_path) if path.exists() and not force]
    if conflicts:
        raise FileExistsError(f"Refusing to overwrite existing delivery: {conflicts[0]}")
    content = bundle.read_bytes()
    delivered_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    receipt = {
        "schema_version": "2.0",
        "kind": "brief-spec-delivery-receipt",
        "status": "delivered",
        "delivery_id": str(uuid.uuid4()),
        "format": "application/zip",
        "destination": {"kind": "local", "locator": str(target.resolve())},
        "content_sha256": sha256_bytes(content),
        "brief_spec_version": None,
        "delivery_schema_version": "2.0",
        "renderer_versions": [],
        "verification_level": "delivered",
        "delivered_at": delivered_at,
    }
    try:
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        receipt["brief_spec_version"] = manifest.get(
            "brief_spec_version", manifest.get("briefspec_version")
        )
        receipt["delivery_schema_version"] = manifest.get("delivery_schema_version")
        receipt["renderer_versions"] = sorted(
            {str(item.get("renderer_version")) for item in manifest.get("files", [])}
        )
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Bundle manifest became unreadable during delivery: {exc}") from exc
    receipt_content = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write_many(
        [
            (target, content, 0o644),
            (receipt_path, receipt_content, 0o644),
        ]
    )
    return {
        "bundle": str(target),
        "receipt": str(receipt_path),
        "sha256": receipt["content_sha256"],
        "delivery_id": receipt["delivery_id"],
    }
