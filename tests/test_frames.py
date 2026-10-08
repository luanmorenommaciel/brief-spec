from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema.validators import validator_for

from briefspec.cli import main
from briefspec.frames import render_frame

ROOT = Path(__file__).resolve().parents[1]


def validate_schema(name: str, value: object) -> None:
    schema = json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text())
    validator = validator_for(schema)
    validator.check_schema(schema)
    validator(schema).validate(value)


def request() -> dict[str, str]:
    return {
        "contract": "BriefSpecFrameRequest/v1",
        "work_type": "research",
        "subject": "workhelm-research",
        "producer": "workhelm",
        "body": "# Research review\n\nEvidence and unresolved questions remain visible.",
    }


def test_frame_is_presentation_only_and_schema_valid(tmp_path: Path) -> None:
    value = request()
    output = tmp_path / "RESEARCH.md"
    receipt = render_frame(value, output=output)

    validate_schema("brief-spec-frame-request", value)
    validate_schema("brief-spec-frame-receipt", receipt)
    assert receipt["contract"] == "BriefSpecFrameReceipt/v1"
    assert receipt["approval_authority"] is False
    assert receipt["dispatch_authority"] is False
    assert receipt["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    rendered = output.read_text(encoding="utf-8")
    assert rendered.startswith("<!-- brief-spec:frame:v1 ")
    assert "producer=workhelm" in rendered
    assert rendered.endswith("<!-- /brief-spec -->\n")


def test_frame_cli_emits_one_machine_receipt(tmp_path: Path, capsys) -> None:
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request()), encoding="utf-8")
    output = tmp_path / "PLAN.md"

    assert main(["frame", str(source), "--output", str(output), "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["contract"] == "BriefSpecFrameReceipt/v1"
    assert Path(receipt["output"]) == output


def test_frame_refuses_implicit_overwrite(tmp_path: Path, capsys) -> None:
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request()), encoding="utf-8")
    output = tmp_path / "OUTCOME.md"
    output.write_text("human-owned\n", encoding="utf-8")

    assert main(["frame", str(source), "--output", str(output), "--json"]) == 1
    assert "Refusing to overwrite" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "human-owned\n"


def test_capabilities_advertise_human_frame_without_authority(capsys) -> None:
    assert main(["capabilities", "all", "--json"]) == 0
    capability = json.loads(capsys.readouterr().out)
    assert capability["contracts"] == {
        "human_frame_request": "BriefSpecFrameRequest/v1",
        "human_frame_receipt": "BriefSpecFrameReceipt/v1",
        "notify_receipt": "brief-spec-notify-receipt/1.0",
        "ack_receipt": "brief-spec-ack-receipt/1.0",
    }
    assert capability["authority"] == {"approval": False, "dispatch": False}
    assert capability["outputs"]["direction"] == "one-way"
    assert capability["outputs"]["network_consent_required"] is True
