from __future__ import annotations

import html
import json
import zipfile
from pathlib import Path

import pytest

from briefspec.bundle import build_delivery_bundle, deliver_bundle
from briefspec.cli import main
from briefspec.delivery import (
    CORE_FORMATS,
    canonical_json_bytes,
    canonical_sha256,
    export_core,
    load_delivery,
    render_html,
    render_markdown,
    render_phone,
    render_spoken_text,
    validate_delivery,
)
from briefspec.markdown import validate_phone
from briefspec.models import VerificationLevel
from briefspec.verification import verify_target

ROOT = Path(__file__).resolve().parents[1]

OUTCOME = """Prelude that must not be exported.
<!-- briefspec:outcome:v1 -->
Status: DONE
Outcome: Canonical delivery is implemented.
Human action: None
Proof:
- [direct/pass kind=file] Contract test at `tests/test_delivery.py`
Gaps: None
Next: None
Open: None
<!-- /briefspec -->
Trailing host transcript that must not be exported.
"""

SPOKEN = """<!-- briefspec:checkpoint:v1 mode=spoken -->
Headline: Delivery recap
Script:
This is a sufficiently useful spoken delivery script. It explains that every Brief-Spec download
comes from one canonical object, that each artifact has an integrity hash, and that the reader can
independently verify the result. It also says that local audio stays offline unless the user
explicitly selects the OpenAI provider and consents to a network request. The next step is to
inspect the generated delivery bundle and compare its manifest against the files it contains.
Screen-only proof:
- [direct/pass kind=file] Contract test at `tests/test_delivery.py`
Next:
- Verify the generated MP3.
<!-- /briefspec -->
"""


def _delivery(text: str = OUTCOME) -> dict[str, object]:
    value, _ = load_delivery(
        text,
        source_path=ROOT / "tests" / "test_delivery.py",
        runtime="codex",
        session_ref="test-session",
        host_version="test-host",
        source_revision="deadbeef",
        created_at="2026-08-11T12:00:00Z",
    )
    return value


def test_markdown_json_and_html_share_one_canonical_delivery(tmp_path: Path) -> None:
    delivery = _delivery()
    validation = validate_delivery(delivery)
    assert validation.valid, validation.errors
    markdown = render_markdown(delivery)
    assert "Prelude" not in markdown and "Trailing" not in markdown
    assert "[direct/pass kind=file]" in markdown
    assert (
        markdown.index("Status: DONE")
        < markdown.index("Outcome: Canonical delivery is implemented.")
        < markdown.index("Human action: None")
        < markdown.index("Type: general + general")
        < markdown.index("### Answer")
        < markdown.index("Proof:")
    )

    records = export_core(delivery, ["markdown", "json", "html"], tmp_path)
    assert {record["format"] for record in records} == {"markdown", "json", "html"}
    assert (tmp_path / "brief.json").read_bytes() == canonical_json_bytes(delivery)
    rendered_html = render_html(delivery)
    assert "Content-Security-Policy" in rendered_html
    assert "data-briefspec-sha256" in rendered_html
    assert "<script" not in rendered_html
    assert "<title>Canonical delivery is implemented.</title>" in rendered_html
    assert (
        rendered_html.index('id="field-status"')
        < rendered_html.index('id="field-outcome"')
        < rendered_html.index('id="field-human_action"')
        < rendered_html.index('id="classification"')
        < rendered_html.index('class="typed"')
        < rendered_html.index('id="field-proof"')
    )

    result = verify_target(
        tmp_path / "brief.html",
        level=VerificationLevel.RENDERED,
        workspace=ROOT,
    )
    assert result["status"] == "PASS", result


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        (
            '<h2 id="field-outcome">Outcome</h2><p>Canonical delivery is implemented.</p>',
            '<h2 id="field-outcome">Outcome</h2><p>Unverified replacement outcome.</p>',
        ),
        ("<h1>Canonical delivery is implemented.</h1>", "<h1>Contradictory headline</h1>"),
        ("[direct/pass kind=file]", "[reported/fail kind=file]"),
        ("</main>", "<p>Additional unsupported report content.</p></main>"),
        ("</style>", "#field-outcome + p {display:none}</style>"),
    ],
    ids=["outcome", "headline", "proof", "injected-content", "hidden-outcome"],
)
def test_standalone_html_visible_tamper(tmp_path: Path, original: str, replacement: str) -> None:
    delivery = _delivery()
    rendered = render_html(delivery)
    assert original in rendered
    target = tmp_path / "brief.html"
    target.write_text(rendered.replace(original, replacement, 1), encoding="utf-8")

    result = verify_target(target, level=VerificationLevel.RENDERED, workspace=ROOT)

    assert result["status"] == "FAIL", result
    assert any(
        check["name"] == "HTML integrity" and check["status"] == "PASS"
        for check in result["checks"]
    ), result


@pytest.mark.parametrize(
    "variant", ["non-object", "unsupported-schema", "invalid-proof", "malformed-classification"]
)
def test_standalone_html_invalid_canonical(tmp_path: Path, variant: str) -> None:
    delivery = _delivery()
    rendered = render_html(delivery)
    original_digest = canonical_sha256(delivery)
    original_payload = html.escape(canonical_json_bytes(delivery).decode("utf-8"))
    if variant == "non-object":
        delivery = None
    elif variant == "unsupported-schema":
        delivery["schema_version"] = "999"
    elif variant == "invalid-proof":
        delivery["brief"]["proof"][0]["basis"] = "reported"
    else:
        delivery["classification"]["work_type"] = []
    # Keep the embedded payload and every declared hash consistent with the invalid input.
    rendered = rendered.replace(
        f"<pre>{original_payload}</pre>",
        f"<pre>{html.escape(canonical_json_bytes(delivery).decode('utf-8'))}</pre>",
    ).replace(original_digest, canonical_sha256(delivery))
    target = tmp_path / "brief.html"
    target.write_text(rendered, encoding="utf-8")

    result = verify_target(target, level=VerificationLevel.RENDERED, workspace=ROOT)

    assert result["status"] == "FAIL", result
    assert any(
        check["name"] == "structure" and check["status"] == "FAIL" for check in result["checks"]
    ), result


@pytest.mark.parametrize(
    "text",
    [
        OUTCOME,
        SPOKEN,
        OUTCOME.replace(
            "Canonical delivery is implemented.", 'Escaped <report> & "quotes" — café.'
        ),
    ],
    ids=["outcome", "spoken", "escaped-unicode"],
)
def test_standalone_html_valid_projection(tmp_path: Path, text: str) -> None:
    delivery = _delivery(text)
    target = tmp_path / "brief.html"
    target.write_text(render_html(delivery), encoding="utf-8")

    result = verify_target(target, level=VerificationLevel.RENDERED, workspace=ROOT)

    assert result["status"] in {"PASS", "WARN"}, result
    assert any(
        check["name"] == "HTML rendering" and check["status"] == "PASS"
        for check in result["checks"]
    ), result


def test_bundle_is_deterministic_and_manifest_is_verified(tmp_path: Path) -> None:
    delivery = _delivery()
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_result = build_delivery_bundle(delivery, first)
    second_result = build_delivery_bundle(delivery, second)
    assert first.read_bytes() == second.read_bytes()
    assert first_result["sha256"] == second_result["sha256"]
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["brief.html", "brief.json", "brief.md", "manifest.json"]
        assert all(item.date_time == (2026, 8, 11, 12, 0, 0) for item in archive.infolist())
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["canonical_sha256"]
    verified = verify_target(first, level=VerificationLevel.RENDERED)
    assert verified["status"] == "PASS", verified


def test_delivery_receipt_attests_to_copied_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "brief.zip"
    build_delivery_bundle(_delivery(), bundle)
    destination = tmp_path / "delivered"
    result = deliver_bundle(bundle, destination)
    receipt = Path(result["receipt"])
    assert receipt.is_file()
    verified = verify_target(receipt, level=VerificationLevel.DELIVERED)
    assert verified["status"] == "PASS", verified


def test_resolved_verification_rejects_workspace_escape(tmp_path: Path) -> None:
    delivery = _delivery()
    delivery["brief"]["proof"][0]["locator"] = str(ROOT / "tests" / "test_delivery.py")
    delivery_path = tmp_path / "brief.json"
    delivery_path.write_bytes(canonical_json_bytes(delivery))
    result = verify_target(
        delivery_path,
        level=VerificationLevel.RESOLVED,
        workspace=tmp_path,
    )
    assert result["status"] == "FAIL"
    assert any("escapes workspace" in check["detail"] for check in result["checks"])


def test_spoken_outputs_are_mode_bounded() -> None:
    spoken = _delivery(SPOKEN)
    assert "canonical object" in render_spoken_text(spoken)
    with pytest.raises(ValueError, match="Spoken Checkpoint"):
        render_spoken_text(_delivery())


def test_cli_export_bundle_verify_and_deliver(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "outcome.md"
    source.write_text(OUTCOME, encoding="utf-8")
    output_dir = tmp_path / "exports"
    assert (
        main(
            [
                "export",
                str(source),
                "--formats",
                "markdown,json,html",
                "--output-dir",
                str(output_dir),
                "--created-at",
                "2026-08-11T12:00:00Z",
                "--json",
            ]
        )
        == 0
    )
    assert len(json.loads(capsys.readouterr().out)["outputs"]) == 3
    bundle = tmp_path / "delivery.zip"
    assert main(["bundle", str(source), "--output", str(bundle), "--json"]) == 0
    capsys.readouterr()
    assert main(["verify", str(bundle), "--level", "rendered", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    delivered = tmp_path / "destination"
    assert main(["deliver", str(bundle), "--to", str(delivered), "--json"]) == 0
    receipt = Path(json.loads(capsys.readouterr().out)["receipt"])
    assert main(["verify", str(receipt), "--level", "delivered"]) == 0


def test_validate_accepts_new_path_first_form(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "outcome.md"
    source.write_text(OUTCOME, encoding="utf-8")
    assert main(["validate", str(source), "--strict"]) == 0
    assert "VALID" in capsys.readouterr().out


PHONE_OUTCOME = """<!-- briefspec:outcome:v1 -->
Status: REVIEW
Outcome: The phone format is ready to read.
Human action: Approve the format change.
Proof:
- [direct/pass kind=file] Contract test at `tests/test_delivery.py`
Gaps:
- Not tried on a real phone.
Next:
- Merge after review.
- Tag later.
Open:
- None
<!-- /briefspec -->
"""


PHONE_ORIENT = """<!-- briefspec:checkpoint:v1 mode=orient -->
Headline: Halfway through the phone work.
Current state: The renderer is written.
Completed:
- Phone renderer
Decisions:
- None
Proof:
- [direct/pass kind=test] `pytest tests/test_delivery.py` passed
Next:
- Review the PR
Open:
- None
<!-- /briefspec -->
"""


def test_phone_render_is_the_four_line_chat_form() -> None:
    delivery = _delivery(PHONE_OUTCOME)
    assert validate_delivery(delivery).valid
    phone = render_phone(delivery)

    assert [line for line in phone.splitlines() if line] == [
        "**Now:** The phone format is ready to read.",
        "**You:** Approve the format change.",
        "**Not yet:** Not tried on a real phone.",
        "**Done:** You, after review.",
    ]
    assert not phone.lstrip().startswith("|")
    for dropped in (
        "<!--",
        "Status",
        "Human action",
        "Next",
        "Proof",
        "Gaps",
        "Open",
        "Type:",
        "Delivery context",
    ):
        assert dropped not in phone
    assert validate_phone(phone).valid

    checkpoint = render_phone(_delivery(PHONE_ORIENT))
    assert [block for block in checkpoint.split("\n\n") if block.strip()][0] == (
        "Halfway through the phone work."
    )
    assert (
        "**Proof:**\n- [direct/pass kind=test] `pytest tests/test_delivery.py` passed" in checkpoint
    )
    assert not [line for line in checkpoint.splitlines() if line.lstrip().startswith("|")]
    assert "<!--" not in checkpoint


def test_phone_is_an_additive_core_format() -> None:
    assert CORE_FORMATS["phone"] == ("brief.phone.md", "text/markdown")
    assert list(CORE_FORMATS)[:3] == ["markdown", "json", "html"]


def _outcome(status: str, human_action: str, gaps: str, next_item: str, open_item: str) -> str:
    return f"""<!-- briefspec:outcome:v1 -->
Status: {status}
Outcome: The phone work reached {status}.
Human action: {human_action}
Proof:
- [direct/pass kind=file] Contract test at `tests/test_delivery.py`
Gaps: {gaps}
Next: {next_item}
Open: {open_item}
<!-- /briefspec -->
"""


@pytest.mark.parametrize(
    ("text", "you", "not_yet", "done"),
    [
        (OUTCOME, "None", "None", "Agent, verified."),
        (
            PHONE_OUTCOME,
            "Approve the format change.",
            "Not tried on a real phone.",
            "You, after review.",
        ),
        (
            _outcome(
                "DECIDE",
                "Pick a scope.",
                "None",
                "Apply the choice.",
                "Options: user; project — Recommendation: user",
            ),
            "Pick a scope.",
            "None",
            "You, once you decide.",
        ),
        (
            _outcome("BLOCKED", "Grant host access.", "The host is down.", "Retry.", "None"),
            "Grant host access.",
            "The host is down.",
            "Agent, once unblocked.",
        ),
        (
            _outcome("FAILED", "None", "The build broke.", "Read the log.", "None"),
            "None",
            "The build broke.",
            "Agent, after another attempt.",
        ),
    ],
    ids=["done", "review", "decide", "blocked", "failed"],
)
def test_phone_done_line_names_who_holds_done(text: str, you: str, not_yet: str, done: str) -> None:
    phone = render_phone(_delivery(text))
    lines = [line for line in phone.splitlines() if line]
    assert [line.split(":**", 1)[0] for line in lines] == [
        "**Now",
        "**You",
        "**Not yet",
        "**Done",
    ]
    assert lines[1:] == [f"**You:** {you}", f"**Not yet:** {not_yet}", f"**Done:** {done}"]
    assert "<!--" not in phone
    assert validate_phone(phone).valid


def test_phone_redirect_points_the_reader_at_the_owner() -> None:
    phone = render_phone(_delivery(PHONE_OUTCOME), owner="  TaskMesh.  ")
    assert [line for line in phone.splitlines() if line] == [
        "**Now:** The phone format is ready to read.",
        "**You:** Open TaskMesh.",
        "**Not yet:** Not tried on a real phone.",
        "**Done:** TaskMesh.",
    ]
    assert validate_phone(phone).valid
    with pytest.raises(ValueError, match="owner"):
        render_phone(_delivery(PHONE_OUTCOME), owner=" . ")


def test_phone_lines_stay_single_line() -> None:
    text = PHONE_OUTCOME.replace(
        "Gaps:\n- Not tried on a real phone.",
        "Gaps:\n- Not tried on a real phone.\n- Not  read   aloud.",
    )
    phone = render_phone(_delivery(text))
    assert "**Not yet:** Not tried on a real phone.; Not read aloud." in phone
    assert len([line for line in phone.splitlines() if line]) == 4


VALID_PHONE = """**Now:** The phone format is ready to read.
**You:** Open Linear.
**Not yet:** None
**Done:** Linear.
"""


def test_validate_phone_accepts_fence_free_chat_briefs() -> None:
    assert validate_phone(VALID_PHONE).valid
    plain = "Now: Shipped.\n\nYou: None\n\nNot yet: None\n\nDone: Agent, verified.\n"
    result = validate_phone(plain)
    assert result.valid, result.errors
    assert result.kind == "phone-brief"
    assert result.data == {
        "Now": "Shipped.",
        "You": "None",
        "Not yet": "None",
        "Done": "Agent, verified.",
    }
    stray = validate_phone(VALID_PHONE + "Thanks!\n")
    assert stray.valid
    assert stray.warnings == ("Line(s) outside the four-line chat form: 5",)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("<!-- briefspec:outcome:v1 -->\n" + VALID_PHONE, "storage fence"),
        (VALID_PHONE + "<!-- /briefspec -->\n", "storage fence"),
        (VALID_PHONE + "<!-- brief-spec:typed:v1 -->\n", "storage fence"),
        (VALID_PHONE + "<!-- note -->\n", "HTML comment"),
        ("| Field | Value |\n| --- | --- |\n| Now | x |\n" + VALID_PHONE, "markdown table"),
        (OUTCOME, "storage fence"),
        ("Intro.\n" + VALID_PHONE, "must lead with Now"),
        (VALID_PHONE.replace("**Done:** Linear.\n", ""), "Missing required field(s): Done"),
        (
            VALID_PHONE.replace("**You:** Open Linear.\n", "") + "**You:** Open Linear.\n",
            "required order",
        ),
        (VALID_PHONE + "**You:** Again.\n", "Duplicate field(s): You"),
        (VALID_PHONE.replace("**Not yet:** None", "**Not yet:**"), "Not yet must not be empty"),
        ("", "must lead with Now"),
    ],
    ids=[
        "outcome-fence",
        "end-fence",
        "typed-fence",
        "html-comment",
        "leading-table",
        "storage-brief",
        "not-leading-now",
        "missing",
        "order",
        "duplicate",
        "empty",
        "blank",
    ],
)
def test_validate_phone_rejects_fences_tables_and_broken_forms(text: str, error: str) -> None:
    result = validate_phone(text)
    assert not result.valid
    assert any(error in item for item in result.errors), result.errors


def test_cli_validate_format_phone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    phone = tmp_path / "brief.phone.md"
    phone.write_text(render_phone(_delivery(PHONE_OUTCOME)), encoding="utf-8")
    assert main(["validate", "--format", "phone", str(phone)]) == 0
    assert "VALID" in capsys.readouterr().out
    assert main(["validate", "outcome", str(phone), "--format", "phone", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "phone-brief"

    fenced = tmp_path / "fenced.md"
    fenced.write_text(PHONE_OUTCOME, encoding="utf-8")
    assert main(["validate", "--format", "phone", str(fenced)]) == 1
    assert "storage fence" in capsys.readouterr().out
    table = tmp_path / "table.md"
    table.write_text("| a | b |\n| - | - |\n" + VALID_PHONE, encoding="utf-8")
    assert main(["validate", "--format", "phone", str(table)]) == 1
    assert "markdown table" in capsys.readouterr().out

    stray = tmp_path / "stray.md"
    stray.write_text(VALID_PHONE + "Thanks!\n", encoding="utf-8")
    assert main(["validate", "--format", "phone", str(stray)]) == 0
    assert main(["validate", "--format", "phone", str(stray), "--strict"]) == 1
    capsys.readouterr()

    assert main(["validate", "checkpoint", str(phone), "--format", "phone"]) == 1
    assert main(["validate", str(phone), "--format", "phone", "--mode", "orient"]) == 1
    assert "Outcome chat briefs only" in capsys.readouterr().err


def test_cli_validate_storage_default_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fenced = tmp_path / "fenced.md"
    fenced.write_text(PHONE_OUTCOME, encoding="utf-8")
    phone = tmp_path / "brief.phone.md"
    phone.write_text(VALID_PHONE, encoding="utf-8")
    assert main(["validate", str(fenced), "--json"]) == 0
    default = capsys.readouterr().out
    assert main(["validate", str(fenced), "--format", "storage", "--json"]) == 0
    assert capsys.readouterr().out == default
    assert json.loads(default)["kind"] == "outcome-brief"
    assert main(["validate", str(phone)]) == 1
    assert "No Brief-Spec marker found" in capsys.readouterr().err


def test_export_selects_phone_and_bundle_adds_it_to_the_required_formats(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "outcome.md"
    source.write_text(PHONE_OUTCOME, encoding="utf-8")
    output_dir = tmp_path / "out"
    assert (
        main(
            [
                "export",
                str(source),
                "--formats",
                "phone",
                "--output-dir",
                str(output_dir),
                "--created-at",
                "2026-08-11T12:00:00Z",
                "--json",
            ]
        )
        == 0
    )
    outputs = json.loads(capsys.readouterr().out)["outputs"]
    assert [(item["format"], item["media_type"]) for item in outputs] == [
        ("phone", "text/markdown")
    ]
    assert [path.name for path in output_dir.iterdir()] == ["brief.phone.md"]

    bundle = tmp_path / "delivery.zip"
    assert (
        main(
            [
                "bundle",
                str(source),
                "--formats",
                "markdown,json,html,phone",
                "--output",
                str(bundle),
                "--created-at",
                "2026-08-11T12:00:00Z",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        phone = archive.read("brief.phone.md").decode("utf-8")
    assert names == {"brief.md", "brief.json", "brief.html", "brief.phone.md", "manifest.json"}
    assert phone.startswith("**Now:** The phone format is ready to read.\n")
    assert main(["verify", str(bundle), "--level", "rendered", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_default_bundle_does_not_require_phone(tmp_path: Path) -> None:
    bundle = tmp_path / "delivery.zip"
    build_delivery_bundle(_delivery(), bundle)
    with zipfile.ZipFile(bundle) as archive:
        assert "brief.phone.md" not in archive.namelist()
