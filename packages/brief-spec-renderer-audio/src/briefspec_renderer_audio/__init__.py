from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from briefspec.delivery import render_spoken_text, sha256_bytes
from briefspec.state import atomic_write_public

__version__ = "0.6.0"


def _tool_version(command: str) -> str:
    result = subprocess.run(
        [command, "-version"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = result.stdout or result.stderr
    return output.splitlines()[0].strip() if output else "unknown"


def _probe_audio(artifact: Path, ffprobe: str) -> tuple[float, set[str], str | None]:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:format_tags=comment:stream=codec_name,codec_type",
            "-of",
            "json",
            str(artifact),
        ],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    try:
        value = json.loads(result.stdout)
        duration = float(value["format"]["duration"])
        codecs = {
            stream.get("codec_name")
            for stream in value.get("streams", [])
            if stream.get("codec_type") == "audio"
        }
        comment = value.get("format", {}).get("tags", {}).get("comment")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid ffprobe output: {exc}") from exc
    return duration, codecs, str(comment) if comment is not None else None


class AudioRenderer:
    name = "audio"
    media_type = "audio/mpeg"
    filename = "brief.mp3"

    def capabilities(self) -> dict[str, Any]:
        tools = {name: shutil.which(name) for name in ("say", "ffmpeg", "ffprobe")}
        return {
            "renderer_version": __version__,
            "media_type": self.media_type,
            "providers": {
                "macos": bool(tools["say"] and tools["ffmpeg"]),
                "openai": bool(os.environ.get("OPENAI_API_KEY") and tools["ffmpeg"]),
            },
            "verification_tools": tools,
            "ready": bool(tools["ffmpeg"] and tools["ffprobe"]),
        }

    def setup(self, *, dry_run: bool = False) -> dict[str, Any]:
        missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
        status = "PASS" if not missing else "WARN"
        return {
            "status": "DRY-RUN" if dry_run else status,
            "detail": "ready" if not missing else f"install manually: {', '.join(missing)}",
        }

    def _macos(self, script: str, target: Path, voice: str, rate: int) -> None:
        say = shutil.which("say")
        if say is None:
            raise RuntimeError("macOS say is unavailable; no cloud fallback was attempted")
        result = subprocess.run(
            [say, "-v", voice, "-r", str(rate), "-o", str(target), script],
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "macOS say failed")

    def _openai(self, script: str, target: Path, voice: str, options: dict[str, Any]) -> str:
        if not options.get("consent_network"):
            raise ValueError("OpenAI audio requires --consent-network")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI audio provider")
        model = str(options.get("model", "gpt-4o-mini-tts"))
        payload = json.dumps(
            {
                "model": model,
                "voice": voice,
                "input": script,
                "response_format": "mp3",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/audio/speech",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                target.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"OpenAI audio request failed with HTTP {exc.code}") from exc
        return model

    def render(
        self,
        delivery: dict[str, Any],
        output: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        script = render_spoken_text(delivery).strip()
        record = render_script_document(
            script,
            output,
            created_at=str(delivery.get("source", {}).get("created_at") or ""),
            provider=str(options.get("provider", "macos")),
            voice=options.get("voice"),
            rate=int(options.get("rate", 190)),
            consent_network=bool(options.get("consent_network")),
            model=str(options.get("model", "gpt-4o-mini-tts")),
        )
        record["path"] = str(output)
        return record

    def verify(self, artifact: Path) -> dict[str, Any]:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            return {"status": "FAIL", "detail": "ffprobe is required for MP3 verification"}
        try:
            duration, codecs, comment = _probe_audio(artifact, ffprobe)
        except (RuntimeError, ValueError) as exc:
            return {"status": "FAIL", "detail": str(exc)}
        if duration <= 0 or "mp3" not in codecs:
            return {"status": "FAIL", "detail": "audio is empty or not MP3"}
        if comment not in {
            "AI-generated speech by Brief-Spec",
            "AI-generated speech by BriefSpec",  # legacy 0.x artifact compatibility
        }:
            return {"status": "FAIL", "detail": "AI-generated speech disclosure is missing"}
        return {
            "status": "PASS",
            "detail": f"MP3 decoded; duration {duration:.2f}s; disclosure verified",
            "duration_seconds": duration,
        }


def render_script_document(
    script: str,
    output: Path,
    *,
    created_at: str,
    provider: str = "macos",
    voice: str | None = None,
    rate: int = 190,
    consent_network: bool = False,
    model: str = "gpt-4o-mini-tts",
    force: bool = False,
) -> dict[str, Any]:
    """Render one canonical spoken script without reading screen-only evidence content."""
    if output.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")
    if not created_at:
        raise ValueError("Audio rendering requires a canonical created_at")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required for Brief-Spec MP3 output")
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe is required for Brief-Spec MP3 verification")
    selected_voice = str(voice or ("marin" if provider == "openai" else "Samantha"))
    if not 80 <= rate <= 400:
        raise ValueError("Audio rate must be between 80 and 400 words per minute")
    renderer = AudioRenderer()
    selected_model: str | None = None
    with tempfile.TemporaryDirectory(prefix="briefspec-audio-") as temporary:
        root = Path(temporary)
        source = root / ("source.aiff" if provider == "macos" else "source.mp3")
        rendered = root / "brief.mp3"
        if provider == "macos":
            renderer._macos(script, source, selected_voice, rate)
        elif provider == "openai":
            selected_model = renderer._openai(
                script,
                source,
                selected_voice,
                {"consent_network": consent_network, "model": model},
            )
        else:
            raise ValueError("Audio provider must be macos or openai")
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                "-metadata",
                "comment=AI-generated speech by Brief-Spec",
                str(rendered),
            ],
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg MP3 conversion failed")
        content = rendered.read_bytes()
        duration, codecs, comment = _probe_audio(rendered, ffprobe)
        if duration <= 0 or "mp3" not in codecs:
            raise RuntimeError("rendered audio is empty or not MP3")
    atomic_write_public(output, content, mode=0o644)
    metadata = {
        "renderer": "audio",
        "renderer_version": __version__,
        "provider": provider,
        "model": selected_model,
        "voice": selected_voice,
        "rate": rate,
        "source_script_sha256": sha256_bytes(script.encode("utf-8")),
        "canonical_created_at": created_at,
        "say_version": (f"macOS {platform.mac_ver()[0]} say" if provider == "macos" else None),
        "ffmpeg_version": _tool_version(ffmpeg),
        "ffprobe_version": _tool_version(ffprobe),
        "duration_seconds": duration,
        "ai_generated": True,
        "disclosure": comment or "AI-generated speech",
    }
    return {
        "format": "audio",
        "path": output.name,
        "media_type": "audio/mpeg",
        "size_bytes": len(content),
        "sha256": sha256_bytes(content),
        "renderer_version": __version__,
        "metadata": metadata,
    }
