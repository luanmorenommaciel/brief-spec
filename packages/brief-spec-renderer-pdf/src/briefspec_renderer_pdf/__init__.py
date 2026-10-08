from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec.delivery import render_html, sha256_bytes
from briefspec.state import atomic_write_public

__version__ = "0.6.0"

_PDF_FIELD = re.compile(r"^(?P<name>[A-Za-z ]+):\s*(?P<value>.+)$", re.MULTILINE)
_PAGE = re.compile(r'<page\s+width="(?P<width>[\d.]+)"\s+height="(?P<height>[\d.]+)">')
_WORD = re.compile(
    r'<word\s+xMin="(?P<x_min>[\d.]+)"\s+yMin="(?P<y_min>[\d.]+)"\s+'
    r'xMax="(?P<x_max>[\d.]+)"\s+yMax="(?P<y_max>[\d.]+)"'
)
_PDF_TIMESTAMP = re.compile(rb"/(?P<field>CreationDate|ModDate) \(D:[^)]+\)")


def _canonicalize_pdf_timestamps(content: bytes, created_at: str) -> bytes:
    """Replace Chromium wall-clock metadata with the canonical delivery timestamp."""
    value = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(UTC)
    timestamp = value.strftime("D:%Y%m%d%H%M%S+00'00'").encode("ascii")

    def replacement(match: re.Match[bytes]) -> bytes:
        return b"/" + match.group("field") + b" (" + timestamp + b")"

    normalized, count = _PDF_TIMESTAMP.subn(replacement, content)
    if count != 2 or len(normalized) != len(content):
        raise RuntimeError("Chromium PDF timestamps could not be normalized deterministically")
    return normalized


def _pdf_fields(output: str) -> dict[str, str]:
    return {
        match.group("name").strip().lower().replace(" ", "_"): match.group("value").strip()
        for match in _PDF_FIELD.finditer(output)
    }


def render_html_document(
    html_content: bytes,
    output: Path,
    *,
    created_at: str,
    title: str,
    page_format: str = "A4",
    force: bool = False,
) -> dict[str, Any]:
    """Render any canonical, self-contained Brief-Spec HTML document to PDF."""
    if page_format not in {"A4", "Letter"}:
        raise ValueError("PDF page format must be A4 or Letter")
    if not created_at:
        raise ValueError("PDF rendering requires a canonical created_at")
    if output.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="briefspec-pdf-") as temporary:
        root = Path(temporary)
        source = root / "brief.html"
        rendered = root / "brief.pdf"
        source.write_bytes(html_content)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser_version = browser.version
            page = browser.new_page()
            page.goto(source.as_uri(), wait_until="load")
            page.pdf(
                path=str(rendered),
                format=page_format,
                print_background=True,
                prefer_css_page_size=False,
            )
            browser.close()
        content = _canonicalize_pdf_timestamps(rendered.read_bytes(), created_at)
    atomic_write_public(output, content, mode=0o644)
    visual_sha256 = None
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        with tempfile.TemporaryDirectory(prefix="briefspec-pdf-visual-") as temporary:
            page = Path(temporary) / "page"
            result = subprocess.run(
                [pdftoppm, "-f", "1", "-singlefile", "-png", str(output), str(page)],
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            rendered_page = page.with_suffix(".png")
            if result.returncode == 0 and rendered_page.is_file():
                visual_sha256 = sha256_bytes(rendered_page.read_bytes())
    metadata = {
        "renderer": "pdf",
        "renderer_version": __version__,
        "source_html_sha256": sha256_bytes(html_content),
        "chromium_version": browser_version,
        "page_format": page_format,
        "canonical_created_at": created_at,
        "title": title,
        "visual_sha256": visual_sha256,
    }
    return {
        "format": "pdf",
        "path": output.name,
        "media_type": "application/pdf",
        "size_bytes": len(content),
        "sha256": sha256_bytes(content),
        "renderer_version": __version__,
        "metadata": metadata,
    }


class PDFRenderer:
    name = "pdf"
    media_type = "application/pdf"
    filename = "brief.pdf"

    def capabilities(self) -> dict[str, Any]:
        try:
            import playwright  # noqa: F401
        except ImportError:
            python_api = False
        else:
            python_api = True
        tools = {
            name: shutil.which(name) for name in ("pdfinfo", "pdftotext", "pdffonts", "pdftoppm")
        }
        return {
            "renderer_version": __version__,
            "media_type": self.media_type,
            "playwright": python_api,
            "verification_tools": tools,
            "ready": python_api and all(tools.values()),
        }

    def setup(self, *, dry_run: bool = False) -> dict[str, Any]:
        command = [sys.executable, "-m", "playwright", "install", "chromium"]
        if dry_run:
            return {"status": "DRY-RUN", "command": command}
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Chromium installation failed")
        return {"status": "PASS", "command": command}

    def render(
        self,
        delivery: dict[str, Any],
        output: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        page_format = str(options.get("page_format", "A4"))
        if page_format not in {"A4", "Letter"}:
            raise ValueError("PDF page format must be A4 or Letter")
        created_at = str(delivery.get("source", {}).get("created_at") or "")
        if not created_at:
            raise ValueError("PDF rendering requires source.created_at")
        html_content = render_html(delivery).encode("utf-8")
        record = render_html_document(
            html_content,
            output,
            created_at=created_at,
            title="Brief-Spec delivery",
            page_format=page_format,
        )
        record["path"] = str(output)
        return record

    def verify(self, artifact: Path) -> dict[str, Any]:
        missing = [
            name
            for name in ("pdfinfo", "pdftotext", "pdffonts", "pdftoppm")
            if shutil.which(name) is None
        ]
        if missing:
            return {"status": "FAIL", "detail": f"missing PDF verifier(s): {', '.join(missing)}"}
        with tempfile.TemporaryDirectory(prefix="briefspec-pdf-verify-") as temporary:
            root = Path(temporary)
            commands = [
                ["pdfinfo", str(artifact)],
                ["pdftotext", str(artifact), str(root / "brief.txt")],
                ["pdffonts", str(artifact)],
                ["pdftotext", "-bbox-layout", str(artifact), str(root / "bbox.html")],
                [
                    "pdftoppm",
                    "-f",
                    "1",
                    "-singlefile",
                    "-png",
                    str(artifact),
                    str(root / "page"),
                ],
            ]
            outputs = []
            for command in commands:
                result = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                if result.returncode != 0:
                    return {
                        "status": "FAIL",
                        "detail": result.stderr.strip() or f"failed: {command[0]}",
                    }
                outputs.append(result.stdout)
            text = (root / "brief.txt").read_text(encoding="utf-8", errors="replace")
            if not text.strip() or not (root / "page.png").is_file():
                return {"status": "FAIL", "detail": "PDF text or first-page render is empty"}
            fields = _pdf_fields(outputs[0])
            try:
                pages = int(fields["pages"])
            except (KeyError, ValueError):
                return {"status": "FAIL", "detail": "PDF page count is missing or invalid"}
            if pages < 1 or fields.get("encrypted", "").lower() != "no":
                return {"status": "FAIL", "detail": "PDF page count or encryption is invalid"}
            if not fields.get("title") or not fields.get("page_size"):
                return {"status": "FAIL", "detail": "PDF title or page size metadata is missing"}
            font_lines = [line for line in outputs[2].splitlines() if line.strip()]
            if len(font_lines) < 3:
                return {"status": "FAIL", "detail": "PDF contains no inspectable embedded font"}
            bbox = (root / "bbox.html").read_text(encoding="utf-8", errors="replace")
            page_match = _PAGE.search(bbox)
            words = list(_WORD.finditer(bbox))
            if page_match is None or not words:
                return {"status": "FAIL", "detail": "PDF text geometry is unavailable"}
            width = float(page_match.group("width"))
            height = float(page_match.group("height"))
            clipped = any(
                float(word.group("x_min")) < 0
                or float(word.group("y_min")) < 0
                or float(word.group("x_max")) > width
                or float(word.group("y_max")) > height
                for word in words
            )
            if clipped:
                return {"status": "FAIL", "detail": "PDF contains text outside the page bounds"}
            visual_sha256 = sha256_bytes((root / "page.png").read_bytes())
        return {
            "status": "PASS",
            "detail": (
                f"{pages} page(s); selectable text, fonts, metadata, geometry, and page render "
                "verified"
            ),
            "page_count": pages,
            "visual_sha256": visual_sha256,
            "page_size": fields["page_size"],
        }
