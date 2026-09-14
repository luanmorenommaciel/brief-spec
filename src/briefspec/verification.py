from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import stat
import subprocess
import tempfile
import urllib.parse
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec.delivery import (
    CORE_FORMATS,
    canonical_sha256,
    load_delivery,
    render_core,
    render_html,
    render_spoken_text,
    sha256_bytes,
    validate_delivery,
)
from briefspec.models import VerificationLevel
from briefspec.renderers import available_renderers

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 128
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_LOCAL_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_NETWORK_REQUESTS = 10
MAX_REDIRECTS = 5
MAX_RESPONSE_HEADERS_BYTES = 64 * 1024
MAX_NETWORK_BODY_BYTES = 16 * 1024 * 1024
NETWORK_TIMEOUT_SECONDS = 10

_LEVEL_ORDER = {
    VerificationLevel.STRUCTURAL: 0,
    VerificationLevel.RESOLVED: 1,
    VerificationLevel.RENDERED: 2,
    VerificationLevel.DELIVERED: 3,
}
_HTML_HASH = re.compile(r'<meta name="(?:brief-spec|briefspec)-sha256" content="([0-9a-f]{64})">')
_HTML_CANONICAL = re.compile(r"<pre>(.*?)</pre>", re.DOTALL)
_SOURCE_LOCATION = re.compile(r"^(?P<path>.+):(?P<line>\d+)(?::(?P<column>\d+))?$")


def _check(checks: list[dict[str, Any]], name: str, status: str, detail: str) -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def _inside(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True


def _source_path(locator: str) -> Path:
    """Accept the common path:line[:column] evidence locator without losing file identity."""
    candidate = Path(locator).expanduser()
    match = _SOURCE_LOCATION.fullmatch(locator)
    if match and not candidate.exists():
        return Path(match.group("path")).expanduser()
    return candidate


def _sha256_file(path: Path, *, maximum: int | None = None) -> str:
    size = path.stat().st_size
    if maximum is not None and size > maximum:
        raise ValueError(f"file exceeds {maximum} byte verification limit: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_preflight(path: Path, archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"bundle exceeds {MAX_INPUT_BYTES} byte input limit")
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"bundle exceeds {MAX_ARCHIVE_MEMBERS} member limit")
    names = [member.filename for member in members]
    if len(names) != len(set(names)):
        raise ValueError("bundle contains duplicate member names")
    total = 0
    for member in members:
        pure = Path(member.filename)
        if (
            pure.is_absolute()
            or member.filename.startswith(("/", "\\"))
            or ".." in pure.parts
            or "\\" in member.filename
        ):
            raise ValueError(f"unsafe archive member path: {member.filename}")
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG} or stat.S_ISLNK(mode):
            raise ValueError(f"special archive member is not allowed: {member.filename}")
        if member.is_dir():
            raise ValueError(f"directory archive member is not allowed: {member.filename}")
        if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError(f"archive member exceeds size limit: {member.filename}")
        total += member.file_size
        if total > MAX_ARCHIVE_EXPANDED_BYTES:
            raise ValueError("archive exceeds total expanded-byte limit")
        if member.file_size and (
            member.compress_size == 0
            or member.file_size / member.compress_size > MAX_COMPRESSION_RATIO
        ):
            raise ValueError(f"archive member exceeds compression-ratio limit: {member.filename}")
    return members


def _delivery_from_target(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"input exceeds {MAX_INPUT_BYTES} byte limit")
        delivery, warnings = load_delivery(path.read_text(encoding="utf-8"), source_path=path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        _check(checks, "structure", "FAIL", str(exc))
        return None, checks
    validation = validate_delivery(delivery)
    _check(
        checks,
        "structure",
        "PASS" if validation.valid else "FAIL",
        "canonical delivery is valid" if validation.valid else "; ".join(validation.errors),
    )
    for warning in (*warnings, *validation.warnings):
        _check(checks, "quality warning", "WARN", warning)
    return delivery, checks


def _verify_html(path: Path, checks: list[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _check(checks, "HTML readability", "FAIL", str(exc))
        return None
    required_csp = ("default-src 'none'", "base-uri 'none'", "form-action 'none'")
    if (
        "Content-Security-Policy" not in content
        or not all(directive in content for directive in required_csp)
        or "<main" not in content
        or "<h1" not in content
    ):
        _check(checks, "HTML semantics", "FAIL", "CSP or semantic landmarks are missing")
        return None
    if re.search(r'<(?:script|link)\b[^>]*(?:src|href)=["\']https?://', content, re.I):
        _check(checks, "HTML offline", "FAIL", "external script or stylesheet reference found")
    else:
        _check(checks, "HTML offline", "PASS", "no external script or stylesheet references")
    hash_match = _HTML_HASH.search(content)
    canonical_match = _HTML_CANONICAL.search(content)
    if hash_match is None or canonical_match is None:
        _check(checks, "HTML integrity", "FAIL", "embedded canonical JSON or hash is missing")
        return None
    try:
        delivery = json.loads(html.unescape(canonical_match.group(1)))
    except json.JSONDecodeError as exc:
        _check(checks, "HTML integrity", "FAIL", f"embedded canonical JSON is invalid: {exc}")
        return None
    try:
        if not isinstance(delivery, dict):
            raise ValueError("canonical delivery must be an object")
        validation = validate_delivery(delivery)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        expected = render_html(delivery)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        _check(checks, "structure", "FAIL", f"embedded canonical delivery is invalid: {exc}")
        return None
    _check(checks, "structure", "PASS", "canonical delivery is valid")
    for warning in validation.warnings:
        _check(checks, "quality warning", "WARN", warning)
    actual = canonical_sha256(delivery)
    declared = hash_match.group(1)
    _check(
        checks,
        "HTML integrity",
        "PASS" if actual == declared else "FAIL",
        f"canonical sha256 {actual}",
    )
    _check(
        checks,
        "HTML rendering",
        "PASS" if content == expected else "FAIL",
        "HTML matches the deterministic rendering of canonical delivery"
        if content == expected
        else "HTML is not the deterministic rendering of canonical delivery",
    )
    return delivery


def _verify_bundle(
    path: Path,
    checks: list[dict[str, Any]],
    *,
    rendered: bool,
    allow_plugins: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    delivery: dict[str, Any] | None = None
    try:
        with zipfile.ZipFile(path) as archive:
            members = _archive_preflight(path, archive)
            names = [member.filename for member in members]
            if "manifest.json" not in names:
                raise ValueError("manifest.json is missing")
            manifest = json.loads(archive.read("manifest.json"))
            expected_names = sorted(item["path"] for item in manifest.get("files", []))
            actual_names = sorted(name for name in names if name != "manifest.json")
            if expected_names != actual_names:
                raise ValueError("bundle contents do not match manifest")
            for item in manifest.get("files", []):
                content = archive.read(item["path"])
                if len(content) != item["size_bytes"]:
                    raise ValueError(f"size mismatch: {item['path']}")
                if sha256_bytes(content) != item["sha256"]:
                    raise ValueError(f"hash mismatch: {item['path']}")
            if "brief.json" in names:
                candidate = json.loads(archive.read("brief.json"))
                validation = validate_delivery(candidate)
                if not validation.valid:
                    raise ValueError(
                        "canonical delivery is invalid: " + "; ".join(validation.errors)
                    )
                if canonical_sha256(candidate) != manifest.get("canonical_sha256"):
                    raise ValueError("canonical delivery hash does not match manifest")
                delivery = candidate
                for output_format, (filename, _) in CORE_FORMATS.items():
                    if filename in names and archive.read(filename) != render_core(
                        delivery, output_format
                    ):
                        raise ValueError(
                            f"{filename} is not the deterministic rendering of brief.json"
                        )
            if rendered and "brief.html" in names:
                rendered_html = archive.read("brief.html").decode("utf-8")
                if not all(
                    directive in rendered_html
                    for directive in ("default-src 'none'", "base-uri 'none'", "form-action 'none'")
                ):
                    raise ValueError("HTML content security policy is not restrictive")
                declared = _HTML_HASH.search(rendered_html)
                embedded = _HTML_CANONICAL.search(rendered_html)
                if declared is None or embedded is None:
                    raise ValueError("HTML canonical payload or hash is missing")
                html_delivery = json.loads(html.unescape(embedded.group(1)))
                if canonical_sha256(html_delivery) != declared.group(1):
                    raise ValueError("HTML canonical hash mismatch")
                if delivery is not None and html_delivery != delivery:
                    raise ValueError("HTML and JSON canonical deliveries differ")
                if re.search(
                    r'<(?:script|link)\b[^>]*(?:src|href)=["\']https?://',
                    rendered_html,
                    re.I,
                ):
                    raise ValueError("HTML contains an external script or stylesheet")
            if rendered:
                declared_plugins = {
                    renderer_name
                    for member, renderer_name in (("brief.pdf", "pdf"), ("brief.mp3", "audio"))
                    if member in names
                }
                if declared_plugins and not allow_plugins:
                    raise ValueError(
                        "renderer plugin verification is disabled; rerun with --allow-plugins"
                    )
                installed = (
                    available_renderers(declared_plugins, official_only=True)
                    if declared_plugins
                    else {}
                )
                for member, renderer_name in (("brief.pdf", "pdf"), ("brief.mp3", "audio")):
                    if member in names and renderer_name not in installed:
                        raise ValueError(f"{renderer_name} renderer is required to verify {member}")
                with tempfile.TemporaryDirectory(prefix="briefspec-bundle-verify-") as temporary:
                    root = Path(temporary)
                    for name, renderer in installed.items():
                        member = renderer.filename
                        if member not in names:
                            continue
                        artifact = root / member
                        artifact.write_bytes(archive.read(member))
                        result = renderer.verify(artifact)
                        if result.get("status") != "PASS":
                            raise ValueError(
                                f"{name} verification failed: {result.get('detail', 'unknown')}"
                            )
                        record = next(
                            item for item in manifest["files"] if item.get("path") == member
                        )
                        metadata = record.get("metadata", {})
                        expected_distribution = f"brief-spec-renderer-{name}"
                        distribution = str(metadata.get("renderer_distribution", "")).lower()
                        if distribution.replace("_", "-") != expected_distribution:
                            raise ValueError(
                                f"{name} renderer distribution metadata is missing or untrusted"
                            )
                        if metadata.get("renderer_entry_point_group") not in {
                            "brief_spec.renderers",
                            "briefspec.renderers",
                        }:
                            raise ValueError(f"{name} renderer entry-point group is invalid")
                        if name == "pdf":
                            expected = sha256_bytes(render_html(delivery).encode("utf-8"))
                            if metadata.get("source_html_sha256") != expected:
                                raise ValueError("PDF source HTML hash does not match brief.json")
                            if metadata.get("visual_sha256") != result.get("visual_sha256"):
                                raise ValueError("PDF rendered-page hash does not match manifest")
                        if name == "audio":
                            expected = sha256_bytes(
                                render_spoken_text(delivery).strip().encode("utf-8")
                            )
                            if metadata.get("source_script_sha256") != expected:
                                raise ValueError(
                                    "audio source Script hash does not match brief.json"
                                )
                            if metadata.get("ai_generated") is not True:
                                raise ValueError("audio AI-generated disclosure is missing")
    except (
        OSError,
        KeyError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        _check(checks, "bundle manifest", "FAIL", str(exc))
        return None, None
    _check(checks, "bundle manifest", "PASS", f"verified {len(expected_names)} file(s)")
    return manifest, delivery


def _public_addresses(hostname: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"hostname resolution failed: {exc}") from exc
    addresses = list(dict.fromkeys(str(info[4][0]).split("%", 1)[0] for info in infos))
    if not addresses:
        raise ValueError("hostname resolved to no addresses")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        unsafe = (
            not address.is_global
            or address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        )
        mapped_unsafe = mapped is not None and (
            not mapped.is_global
            or mapped.is_loopback
            or mapped.is_private
            or mapped.is_link_local
            or mapped.is_multicast
            or mapped.is_unspecified
            or mapped.is_reserved
        )
        if unsafe or mapped_unsafe:
            raise ValueError(f"network target is not globally routable: {address}")
    return addresses


def _network_request(
    locator: str,
    *,
    expected_sha256: str | None,
    request_budget: list[int],
) -> tuple[int, str, str | None]:
    current = locator
    redirects = 0
    while True:
        if request_budget[0] >= MAX_NETWORK_REQUESTS:
            raise ValueError(f"delivery exceeds {MAX_NETWORK_REQUESTS} network request limit")
        request_budget[0] += 1
        parsed = urllib.parse.urlsplit(current)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("only absolute HTTP(S) URLs are supported")
        if parsed.username or parsed.password or any(char in current for char in "\r\n"):
            raise ValueError("URL credentials and control characters are not allowed")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = _public_addresses(parsed.hostname, port)
        address = addresses[0]
        raw_socket = socket.create_connection(
            (address, port),
            timeout=NETWORK_TIMEOUT_SECONDS,
        )
        connection: socket.socket | ssl.SSLSocket = raw_socket
        try:
            if parsed.scheme == "https":
                connection = ssl.create_default_context().wrap_socket(
                    raw_socket,
                    server_hostname=parsed.hostname,
                )
            target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            host_header = parsed.hostname
            if parsed.port and parsed.port not in {80, 443}:
                host_header = f"{host_header}:{parsed.port}"
            method = "GET" if expected_sha256 else "HEAD"
            request = (
                f"{method} {target} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "User-Agent: Brief-Spec-Verifier/0.5\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            connection.sendall(request)
            response = http.client.HTTPResponse(connection)
            response.begin()
            header_bytes = sum(
                len(name.encode("latin-1")) + len(value.encode("latin-1")) + 4
                for name, value in response.getheaders()
            )
            if header_bytes > MAX_RESPONSE_HEADERS_BYTES:
                raise ValueError("response headers exceed 64 KiB")
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("redirect response omitted Location")
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    raise ValueError(f"URL exceeds {MAX_REDIRECTS} redirect limit")
                current = urllib.parse.urljoin(current, location)
                continue
            if response.status >= 400:
                raise ValueError(f"HTTP status {response.status}")
            actual_sha256 = None
            if expected_sha256:
                content = response.read(MAX_NETWORK_BODY_BYTES + 1)
                if len(content) > MAX_NETWORK_BODY_BYTES:
                    raise ValueError("response body exceeds 16 MiB hash-verification limit")
                actual_sha256 = hashlib.sha256(content).hexdigest()
            return response.status, current, actual_sha256
        finally:
            connection.close()


def resolve_public_url(locator: str, *, expected_sha256: str | None = None) -> dict[str, Any]:
    """Resolve one public HTTP(S) locator with Brief-Spec's SSRF and size safeguards."""
    status, final_url, actual_sha256 = _network_request(
        locator,
        expected_sha256=expected_sha256,
        request_budget=[0],
    )
    return {
        "status": status,
        "final_url": final_url,
        "sha256": actual_sha256,
    }


def _resolve_delivery(
    delivery: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    workspace: Path,
    consent_network: bool,
    allow_outside_workspace: bool,
    allow_large_artifact: bool,
) -> None:
    evidence = delivery.get("brief", {}).get("proof", [])
    references = [item for item in evidence if isinstance(item, dict)]
    references.extend(item for item in delivery.get("provenance", []) if isinstance(item, dict))
    references.extend(item for item in delivery.get("artifacts", []) if isinstance(item, dict))
    now = datetime.now(UTC)
    request_budget = [0]
    for index, item in enumerate(references, start=1):
        locator = str(item.get("locator", ""))
        inferred_kind = "url" if locator.startswith(("https://", "http://")) else "observation"
        kind = str(
            item.get("kind")
            or (
                inferred_kind
                if inferred_kind == "url"
                else ("artifact" if "artifact_id" in item else inferred_kind)
            )
        )
        expires_at = item.get("expires_at")
        if expires_at:
            try:
                expired = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")) < now
            except ValueError:
                _check(checks, f"reference {index}", "FAIL", "invalid expiry timestamp")
                continue
            if expired:
                _check(checks, f"reference {index}", "FAIL", f"expired artifact: {locator}")
                continue
        if kind in {"file", "artifact"}:
            candidate = _source_path(locator)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            resolved = candidate.resolve(strict=False)
            if not allow_outside_workspace and not _inside(resolved, workspace):
                _check(checks, f"reference {index}", "FAIL", f"path escapes workspace: {locator}")
                continue
            if not resolved.is_file():
                _check(checks, f"reference {index}", "FAIL", f"file not found: {locator}")
                continue
            declared = item.get("sha256")
            try:
                actual = _sha256_file(
                    resolved,
                    maximum=None if allow_large_artifact else MAX_LOCAL_ARTIFACT_BYTES,
                )
            except (OSError, ValueError) as exc:
                _check(checks, f"reference {index}", "FAIL", str(exc))
                continue
            if declared and declared != actual:
                _check(checks, f"reference {index}", "FAIL", f"hash mismatch: {locator}")
            else:
                _check(checks, f"reference {index}", "PASS", f"file resolved: {locator}")
        elif kind == "commit":
            result = subprocess.run(
                ["git", "cat-file", "-e", f"{locator}^{{commit}}"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            _check(
                checks,
                f"reference {index}",
                "PASS" if result.returncode == 0 else "FAIL",
                f"commit {'resolved' if result.returncode == 0 else 'missing'}: {locator}",
            )
        elif kind == "url":
            if item.get("access") == "private":
                _check(
                    checks,
                    f"reference {index}",
                    "WARN",
                    f"private URL requires authorized access; unresolved: {locator}",
                )
                continue
            if not consent_network:
                _check(
                    checks,
                    f"reference {index}",
                    "WARN",
                    f"network consent not granted; URL declared but unresolved: {locator}",
                )
                continue
            try:
                expected = item.get("content_sha256") or (
                    item.get("sha256") if kind == "url" else None
                )
                status, final_url, actual_sha256 = _network_request(
                    locator,
                    expected_sha256=str(expected) if expected else None,
                    request_budget=request_budget,
                )
            except (OSError, ValueError, ssl.SSLError) as exc:
                _check(checks, f"reference {index}", "FAIL", f"URL unresolved: {exc}")
            else:
                if expected and actual_sha256 != expected:
                    _check(
                        checks,
                        f"reference {index}",
                        "FAIL",
                        f"URL publicly reachable but content hash mismatched: {final_url}",
                    )
                else:
                    guarantee = "content hash matched" if expected else "publicly reachable"
                    _check(
                        checks,
                        f"reference {index}",
                        "PASS",
                        f"URL {guarantee} (HTTP {status}): {final_url}",
                    )
        elif kind == "command":
            _check(checks, f"reference {index}", "WARN", "command evidence is never executed")
        else:
            _check(checks, f"reference {index}", "WARN", f"unresolved {kind}: {locator}")


def _verify_receipt(path: Path, checks: list[dict[str, Any]]) -> None:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("kind") not in {
            "brief-spec-delivery-receipt",
            "briefspec-delivery-receipt",
        }:
            raise ValueError("not a Brief-Spec delivery receipt")
        if receipt.get("status") != "delivered" or not receipt.get("delivered_at"):
            raise ValueError("receipt is not delivered")
        locator = receipt["destination"]["locator"]
        destination = Path(locator)
        if not destination.is_file():
            raise ValueError(f"delivered file is missing: {destination}")
        actual = _sha256_file(destination, maximum=MAX_INPUT_BYTES)
        if actual != receipt.get("content_sha256"):
            raise ValueError("delivered content hash does not match receipt")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _check(checks, "delivery receipt", "FAIL", str(exc))
        return
    _check(checks, "delivery receipt", "PASS", f"delivered content verified: {destination}")


def verify_bundle_integrity(target: Path, *, allow_plugins: bool = False) -> dict[str, Any]:
    """Verify a bundle's manifest and deterministic renderings without resolving sources."""
    checks: list[dict[str, Any]] = []
    _verify_bundle(target.resolve(), checks, rendered=True, allow_plugins=allow_plugins)
    status = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    return {"target": str(target.resolve()), "status": status, "checks": checks}


def verify_target(
    target: Path,
    *,
    level: VerificationLevel = VerificationLevel.STRUCTURAL,
    workspace: Path | None = None,
    offline: bool | None = None,
    consent_network: bool = False,
    allow_plugins: bool = False,
    allow_outside_workspace: bool = False,
    allow_large_artifact: bool = False,
) -> dict[str, Any]:
    target = target.resolve()
    workspace = (workspace or Path.cwd()).resolve()
    checks: list[dict[str, Any]] = []
    delivery: dict[str, Any] | None = None
    suffix = target.suffix.lower()
    network_allowed = consent_network and offline is not True
    if not target.is_file():
        _check(checks, "target", "FAIL", f"file does not exist: {target}")
    elif target.stat().st_size > MAX_INPUT_BYTES:
        _check(checks, "target", "FAIL", f"input exceeds {MAX_INPUT_BYTES} byte limit")
    elif target.name.endswith(".receipt.json"):
        _verify_receipt(target, checks)
    elif suffix == ".zip":
        _, delivery = _verify_bundle(
            target,
            checks,
            rendered=_LEVEL_ORDER[level] >= _LEVEL_ORDER[VerificationLevel.RENDERED],
            allow_plugins=allow_plugins,
        )
    elif suffix == ".html":
        delivery = _verify_html(target, checks)
    elif suffix in {".pdf", ".mp3"}:
        renderer_name = "pdf" if suffix == ".pdf" else "audio"
        if not allow_plugins:
            _check(
                checks,
                "renderer",
                "FAIL",
                "renderer plugin verification is disabled; rerun with --allow-plugins",
            )
            renderer = None
        else:
            renderer = available_renderers({renderer_name}, official_only=True).get(renderer_name)
        if allow_plugins and renderer is None:
            _check(checks, "renderer", "FAIL", f"{renderer_name} renderer is not installed")
        elif renderer is not None:
            result = renderer.verify(target)
            _check(
                checks,
                f"{renderer_name} render",
                str(result.get("status", "FAIL")),
                str(result.get("detail", "renderer returned no detail")),
            )
    else:
        delivery, parsed_checks = _delivery_from_target(target)
        checks.extend(parsed_checks)

    if delivery is not None and _LEVEL_ORDER[level] >= _LEVEL_ORDER[VerificationLevel.RESOLVED]:
        _resolve_delivery(
            delivery,
            checks,
            workspace=workspace,
            consent_network=network_allowed,
            allow_outside_workspace=allow_outside_workspace,
            allow_large_artifact=allow_large_artifact,
        )
    if level is VerificationLevel.DELIVERED and not target.name.endswith(".receipt.json"):
        receipt = target.with_suffix(target.suffix + ".receipt.json")
        if receipt.is_file():
            _verify_receipt(receipt, checks)
        else:
            _check(checks, "delivery receipt", "FAIL", f"receipt not found: {receipt}")
    status = (
        "FAIL"
        if any(item["status"] == "FAIL" for item in checks)
        else ("WARN" if any(item["status"] == "WARN" for item in checks) else "PASS")
    )
    return {
        "target": str(target),
        "level": level.value,
        "network_consent": network_allowed,
        "plugins_allowed": allow_plugins,
        "status": status,
        "checks": checks,
    }
