#!/usr/bin/env python3
"""Exercise an installed optional renderer from canonical input through verification."""

from __future__ import annotations

import argparse
import tempfile
import zipfile
from pathlib import Path

from briefspec.cli import main

OUTCOME = """<!-- briefspec:outcome:v1 -->
Status: DONE
Outcome: The PDF delivery renderer completed its end-to-end smoke test.
Human action: None
Proof:
- [direct/pass kind=file] Renderer smoke at `scripts/run-renderer-smoke.py`
Gaps: None
Next: None
Open: None
<!-- /briefspec -->
"""

SPOKEN = """<!-- briefspec:checkpoint:v1 mode=spoken -->
Headline: Audio delivery smoke test
Script:
This is the Brief-Spec audio delivery smoke test. The renderer is reading only the bounded spoken
script, producing a portable MP3 file, and checking that the resulting audio stream can be decoded.
The bundle keeps its screen-only evidence separate from these spoken words. Local macOS speech
stays offline, and Brief-Spec never changes providers without a direct request from the user. The
verification step checks the codec and duration before the artifact can be described as rendered.
This same canonical script can also produce spoken text and SSML, which lets every download carry
the same meaning. Metadata records the selected voice, source hash, tool versions, and measured
duration, while credentials and screen-only proof stay out of the audio. A listener therefore gets
a concise status update without losing the integrity trail available in the adjacent manifest.
Screen-only proof:
- [direct/pass kind=file] Renderer smoke at `scripts/run-renderer-smoke.py`
Next:
- Retain the renderer result with the release evidence.
<!-- /briefspec -->
"""


def main_command() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("renderer", choices=["pdf", "audio"])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="briefspec-renderer-smoke-") as temporary:
        root = Path(temporary)
        source = root / "brief.md"
        source.write_text(OUTCOME if args.renderer == "pdf" else SPOKEN, encoding="utf-8")
        export_args = [
            "export",
            str(source),
            "--formats",
            args.renderer,
            "--output-dir",
            str(root / "out"),
            "--created-at",
            "2026-08-11T12:00:00Z",
        ]
        if args.renderer == "audio":
            export_args.extend(["--audio-provider", "macos", "--voice", "Samantha"])
        if main(export_args) != 0:
            return 1
        artifact = root / "out" / ("brief.pdf" if args.renderer == "pdf" else "brief.mp3")
        if main(["verify", str(artifact), "--level", "rendered", "--allow-plugins"]) != 0:
            return 1
        bundle_args = [
            "bundle",
            str(source),
            "--formats",
            args.renderer,
            "--output",
            str(root / "delivery.zip"),
            "--created-at",
            "2026-08-11T12:00:00Z",
        ]
        if args.renderer == "audio":
            bundle_args.extend(["--audio-provider", "macos", "--voice", "Samantha"])
        if main(bundle_args) != 0:
            return 1
        with zipfile.ZipFile(root / "delivery.zip") as archive:
            bundled_name = "brief.pdf" if args.renderer == "pdf" else "brief.mp3"
            if args.renderer == "pdf" and artifact.read_bytes() != archive.read(bundled_name):
                raise RuntimeError(
                    "repeated canonical pdf rendering was not byte-identical"
                )
        return main(
            [
                "verify",
                str(root / "delivery.zip"),
                "--level",
                "rendered",
                "--allow-plugins",
            ]
        )


if __name__ == "__main__":
    raise SystemExit(main_command())
