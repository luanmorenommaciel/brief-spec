from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema.validators import validator_for
from referencing import Registry, Resource

from briefspec.cli import main
from briefspec.delivery import (
    canonical_json_bytes,
    load_delivery,
    render_core,
    render_html,
    render_markdown,
    render_phone,
    validate_delivery,
)
from briefspec.markdown import (
    parse_source,
    render_source,
    source_gaps,
    validate_outcome,
    validate_phone,
)
from briefspec.models import OutcomeSource, SourceStatus

ROOT = Path(__file__).resolve().parents[1]
CREATED_AT = "2026-11-26T12:00:00Z"
PROOF = "- [direct/pass kind=test] `pytest tests/test_sources.py` passed"


def _brief(
    status: str,
    *,
    sources: str | None = None,
    human_action: str = "None",
    gaps: str = "None",
    next_items: str = "None",
) -> str:
    lines = [
        "<!-- briefspec:outcome:v1 -->",
        f"Status: {status}",
        "Outcome: The weekly revenue report was regenerated.",
        f"Human action: {human_action}",
        "Proof:",
        PROOF,
        f"Gaps: {gaps}",
        f"Next: {next_items}",
        "Open: None",
    ]
    if sources is not None:
        lines.append(sources)
    lines.append("<!-- /briefspec -->")
    return "\n".join(lines) + "\n"


def _sources(*items: str) -> str:
    return "\n".join(["Sources:", *(f"- {item}" for item in items)])


def _delivery(text: str) -> dict[str, Any]:
    value, _ = load_delivery(text, runtime="codex", created_at=CREATED_AT)
    return value


def _gap_items(markdown: str) -> list[str]:
    lines = markdown.splitlines()
    start = lines.index("Gaps:") + 1
    items: list[str] = []
    for line in lines[start:]:
        if not line.startswith("- "):
            break
        items.append(line[2:])
    return items


def _not_yet(phone: str) -> str:
    result = validate_phone(phone)
    assert result.valid, result.errors
    assert not result.warnings, result.warnings
    return result.data["Not yet"]


# Done when 1: a required non-ok source fails DONE, naming the source and status.


@pytest.mark.parametrize("status", ["stale", "missing", "error"])
def test_required_non_ok_source_blocks_done_by_name_and_status(status: str) -> None:
    text = _brief("DONE", sources=_sources("warehouse.orders: ok", f"crm.accounts: {status}"))
    result = validate_outcome(text)
    assert not result.valid
    [error] = result.errors
    assert "crm.accounts" in error
    assert status in error
    assert "warehouse.orders" not in error


def test_required_source_error_includes_the_note() -> None:
    text = _brief("DONE", sources=_sources("crm.accounts: missing — export job never ran"))
    [error] = validate_outcome(text).errors
    assert error.startswith("DONE cannot rely on required source crm.accounts, which is missing")
    assert "export job never ran" in error


def test_every_blocking_required_source_is_named() -> None:
    text = _brief(
        "DONE",
        sources=_sources("crm.accounts: missing", "billing.events: error — 500 from API"),
    )
    errors = validate_outcome(text).errors
    assert len(errors) == 2
    assert "crm.accounts, which is missing" in errors[0]
    assert "billing.events, which is error: 500 from API" in errors[1]


def test_explicit_required_marker_still_blocks_done() -> None:
    text = _brief("DONE", sources=_sources("crm.accounts: stale (required)"))
    assert not validate_outcome(text).valid


def test_json_delivery_with_required_non_ok_source_fails_done() -> None:
    delivery = _delivery(_brief("REVIEW", human_action="Re-run export.", sources=_sources("a: ok")))
    delivery["brief"]["status"] = "DONE"
    delivery["brief"]["human_action"] = None
    delivery["brief"]["sources"] = [{"name": "crm.accounts", "status": "missing"}]
    result = validate_delivery(delivery)
    assert not result.valid
    assert any("crm.accounts, which is missing" in error for error in result.errors)


# Done when 2: the same brief passes when not DONE and lists the source under Gaps / Not yet.


@pytest.mark.parametrize("status", ["stale", "missing", "error"])
def test_required_non_ok_source_passes_when_not_done_and_surfaces_everywhere(
    status: str,
) -> None:
    sources = _sources("warehouse.orders: ok", f"crm.accounts: {status} — export job never ran")
    text = _brief("REVIEW", human_action="Re-run the CRM export.", sources=sources)
    result = validate_outcome(text)
    assert result.valid, result.errors

    delivery = _delivery(text)
    assert validate_delivery(delivery).valid
    expected = f"Source crm.accounts: {status} — export job never ran"

    markdown = render_markdown(delivery)
    assert _gap_items(markdown) == [expected]
    assert "warehouse.orders" not in "\n".join(_gap_items(markdown))

    phone = render_phone(delivery)
    assert _not_yet(phone) == expected
    assert phone.count("**Not yet:**") == 1

    assert expected in render_html(delivery)


def test_storage_keeps_author_gaps_ahead_of_source_gaps() -> None:
    text = _brief(
        "REVIEW",
        human_action="Approve the report.",
        gaps="\n- Currency rounding not reviewed.",
        sources=_sources("crm.accounts: missing"),
    )
    delivery = _delivery(text)
    assert delivery["brief"]["gaps"] == ["Currency rounding not reviewed."]
    assert _gap_items(render_markdown(delivery)) == [
        "Currency rounding not reviewed.",
        "Source crm.accounts: missing",
    ]
    assert _not_yet(render_phone(delivery)) == (
        "Currency rounding not reviewed.; Source crm.accounts: missing"
    )


def test_phone_format_from_cli_lists_the_source_under_not_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "outcome.md"
    source.write_text(
        _brief(
            "BLOCKED",
            human_action="Restore CRM credentials.",
            next_items="Re-run the export once credentials are back.",
            sources=_sources("crm.accounts: error — 401 from CRM"),
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    assert (
        main(
            [
                "export",
                str(source),
                "--formats",
                "markdown,phone",
                "--output-dir",
                str(out),
                "--created-at",
                CREATED_AT,
            ]
        )
        == 0
    )
    phone = (out / "brief.phone.md").read_text(encoding="utf-8")
    assert _not_yet(phone) == "Source crm.accounts: error — 401 from CRM"
    markdown = (out / "brief.md").read_text(encoding="utf-8")
    assert _gap_items(markdown) == ["Source crm.accounts: error — 401 from CRM"]

    capsys.readouterr()
    phone_path = str(out / "brief.phone.md")
    assert main(["validate", "--format", "phone", "--strict", "--json", phone_path]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["data"]["Not yet"] == "Source crm.accounts: error — 401 from CRM"


def test_a_non_ok_source_satisfies_the_blocked_gap_requirement() -> None:
    text = _brief(
        "BLOCKED",
        human_action="Restore CRM credentials.",
        next_items="Re-run the export.",
        sources=_sources("crm.accounts: error"),
    )
    assert validate_outcome(text).valid
    without = _brief(
        "BLOCKED",
        human_action="Restore CRM credentials.",
        next_items="Re-run the export.",
        sources=_sources("crm.accounts: ok"),
    )
    assert "BLOCKED requires an explicit gap" in validate_outcome(without).errors


# Done when 3: an optional non-ok source surfaces without blocking DONE.


@pytest.mark.parametrize("status", ["stale", "missing", "error"])
def test_optional_non_ok_source_surfaces_without_blocking_done(status: str) -> None:
    text = _brief(
        "DONE",
        sources=_sources("warehouse.orders: ok", f"fx.rates: {status} (optional) — 3 days old"),
    )
    result = validate_outcome(text)
    assert result.valid, result.errors

    delivery = _delivery(text)
    assert validate_delivery(delivery).valid
    expected = f"Source fx.rates: {status} (optional) — 3 days old"
    assert _gap_items(render_markdown(delivery)) == [expected]
    phone = render_phone(delivery)
    assert _not_yet(phone) == expected
    assert "**Done:** Agent, verified." in phone
    assert expected in render_html(delivery)


def test_optional_gap_does_not_block_done_after_a_render_round_trip() -> None:
    delivery = _delivery(_brief("DONE", sources=_sources("fx.rates: stale (optional)")))
    rendered = render_markdown(delivery)
    assert "- Source fx.rates: stale (optional)" in rendered
    reparsed = validate_outcome(rendered)
    assert reparsed.valid, reparsed.errors
    assert _delivery(rendered)["brief"] == delivery["brief"]


def test_optional_non_ok_source_does_not_hide_a_required_one() -> None:
    text = _brief(
        "DONE",
        sources=_sources("fx.rates: stale (optional)", "crm.accounts: missing"),
    )
    [error] = validate_outcome(text).errors
    assert "crm.accounts" in error and "fx.rates" not in error


def test_author_written_gaps_still_block_done_when_sources_are_present() -> None:
    text = _brief(
        "DONE",
        gaps="\n- Rounding not reviewed.",
        sources=_sources("fx.rates: stale (optional)"),
    )
    assert "DONE cannot contain unresolved gaps" in validate_outcome(text).errors


def test_compact_done_accepts_trailing_sources() -> None:
    text = "\n".join(
        [
            "<!-- briefspec:outcome:v1 -->",
            "Status: DONE",
            "Outcome: The report was regenerated.",
            "Proof: [direct/pass] `pytest tests/test_sources.py` passed",
            "Sources:",
            "- warehouse.orders: ok",
            "- fx.rates: stale (optional)",
            "<!-- /briefspec -->",
        ]
    )
    result = validate_outcome(text)
    assert result.valid, result.errors
    assert result.data["Form"] == "compact"
    assert _gap_items(render_markdown(_delivery(text))) == ["Source fx.rates: stale (optional)"]
    assert _not_yet(render_phone(_delivery(text))) == "Source fx.rates: stale (optional)"
    blocked = text.replace("- warehouse.orders: ok", "- warehouse.orders: missing")
    assert not validate_outcome(blocked).valid


# Done when 4: briefs that omit sources behave exactly as before.

NO_SOURCES = _brief("REVIEW", human_action="Approve the report.", gaps="\n- Rounding not reviewed.")


def test_omitted_sources_leave_gaps_and_phone_unchanged() -> None:
    result = validate_outcome(NO_SOURCES)
    assert result.valid
    assert "Sources" not in result.data
    assert result.data["Gaps"] == ["Rounding not reviewed."]

    delivery = _delivery(NO_SOURCES)
    assert "sources" not in delivery["brief"]
    markdown = render_markdown(delivery)
    assert "Sources" not in markdown
    assert _gap_items(markdown) == ["Rounding not reviewed."]
    phone = render_phone(delivery)
    assert phone == (
        "**Now:** The weekly revenue report was regenerated.\n\n"
        "**You:** Approve the report.\n\n"
        "**Not yet:** Rounding not reviewed.\n\n"
        "**Done:** You, after review.\n"
    )
    assert _not_yet(phone) == "Rounding not reviewed."
    assert "Not yet" not in render_html(delivery)


def test_sources_none_reads_the_same_as_omitting_the_field() -> None:
    with_none = _brief(
        "REVIEW",
        human_action="Approve the report.",
        gaps="\n- Rounding not reviewed.",
        sources="Sources: None",
    )
    assert validate_outcome(with_none).data == validate_outcome(NO_SOURCES).data
    for output_format in ("markdown", "json", "html", "phone"):
        assert render_core(_delivery(with_none), output_format) == render_core(
            _delivery(NO_SOURCES), output_format
        )


def test_all_ok_sources_keep_done_green_without_gaps() -> None:
    text = _brief("DONE", sources=_sources("warehouse.orders: ok", "fx.rates: ok (optional)"))
    assert validate_outcome(text).valid
    delivery = _delivery(text)
    markdown = render_markdown(delivery)
    assert "Gaps: None" in markdown
    assert _not_yet(render_phone(delivery)) == "None"
    assert delivery["brief"]["sources"] == [
        {"name": "warehouse.orders", "status": "ok", "required": True},
        {"name": "fx.rates", "status": "ok", "required": False},
    ]


# Parsing, ordering, and JSON contract.


def test_source_lines_round_trip_through_the_model() -> None:
    for line in (
        "warehouse.orders: ok",
        "s3://bucket/exports: missing — nightly job skipped",
        "fx.rates: stale (optional) — 3 days old",
        "api: v2: error",
    ):
        source = parse_source(line)
        assert source is not None
        assert render_source(source) == line
        assert OutcomeSource.from_dict(source.to_dict()) == source
    assert parse_source("api: v2: error") == OutcomeSource("api: v2", SourceStatus.ERROR)
    assert parse_source("CRM: Missing -- token expired") == OutcomeSource(
        "CRM", SourceStatus.MISSING, note="token expired"
    )
    assert source_gaps([OutcomeSource("a", SourceStatus.OK)]) == []


@pytest.mark.parametrize("line", ["crm.accounts", "crm.accounts: unknown", ": ok", "crm: ok ok"])
def test_malformed_source_lines_fail_closed(line: str) -> None:
    result = validate_outcome(_brief("REVIEW", human_action="Look.", sources=_sources(line)))
    assert not result.valid
    assert any(error.startswith("Sources item 1 must read") for error in result.errors)


def test_duplicate_source_names_are_rejected() -> None:
    result = validate_outcome(
        _brief("REVIEW", human_action="Look.", sources=_sources("crm: ok", "crm: missing"))
    )
    assert "Sources item 2 repeats source crm" in result.errors


def test_sources_must_follow_the_required_fields() -> None:
    text = _brief("DONE").replace("Gaps: None", "Sources:\n- crm: ok\nGaps: None")
    assert "Fields are not in the required order" in validate_outcome(text).errors


def test_json_brief_with_sources_round_trips_through_markdown() -> None:
    delivery = _delivery(
        _brief(
            "REVIEW",
            human_action="Re-run the export.",
            sources=_sources("warehouse.orders: ok", "crm.accounts: missing — job skipped"),
        )
    )
    reloaded, _ = load_delivery(canonical_json_bytes(delivery).decode("utf-8"))
    assert reloaded == delivery
    from_markdown = _delivery(render_markdown(delivery))
    assert from_markdown["brief"] == delivery["brief"]


def test_json_sources_may_omit_required_and_default_to_required() -> None:
    delivery = _delivery(_brief("REVIEW", human_action="Look.", sources=_sources("a: ok")))
    delivery["brief"]["sources"] = [{"name": "crm.accounts", "status": "stale"}]
    assert validate_delivery(delivery).valid
    assert "- Source crm.accounts: stale" in render_markdown(delivery)


@pytest.mark.parametrize(
    ("sources", "message"),
    [
        ("crm", "brief.sources must be an array"),
        (["crm"], "brief.sources[0] must be an object"),
        ([{"name": "crm", "status": "gone"}], "brief.sources[0].status must be one of"),
        ([{"name": "", "status": "ok"}], "brief.sources[0].name must be a non-empty"),
        ([{"name": "a\nb", "status": "ok"}], "brief.sources[0].name must be a non-empty"),
        ([{"name": "crm", "status": "ok", "required": "yes"}], "required must be a boolean"),
        ([{"name": "crm", "status": "ok", "note": ""}], "note must be null or a non-empty"),
        ([{"name": "crm", "status": "ok", "owner": "x"}], "unsupported field(s): owner"),
        (
            [{"name": "crm", "status": "ok"}, {"name": "crm", "status": "ok"}],
            "brief.sources[1].name repeats source crm",
        ),
    ],
)
def test_malformed_json_sources_are_rejected(sources: Any, message: str) -> None:
    delivery = _delivery(_brief("REVIEW", human_action="Look.", sources=_sources("a: ok")))
    delivery["brief"]["sources"] = sources
    result = validate_delivery(delivery)
    assert not result.valid
    assert any(message in error for error in result.errors), result.errors


def test_json_sources_that_do_not_survive_markdown_are_rejected() -> None:
    delivery = _delivery(_brief("REVIEW", human_action="Look.", sources=_sources("a: ok")))
    delivery["brief"]["sources"] = [{"name": "crm: ok — legacy", "status": "ok"}]
    result = validate_delivery(delivery)
    assert "Delivery brief.sources does not survive Markdown rendering" in result.errors


def test_checkpoints_cannot_declare_sources() -> None:
    checkpoint = (
        "<!-- briefspec:checkpoint:v1 mode=orient -->\n"
        "Headline: Halfway.\nCurrent state: Working.\nCompleted: None\nDecisions: None\n"
        f"Proof:\n{PROOF}\nNext:\n- Continue.\nOpen: None\n<!-- /briefspec -->\n"
    )
    delivery = _delivery(checkpoint)
    delivery["brief"]["sources"] = [{"name": "crm", "status": "ok"}]
    assert "Only an outcome brief may declare brief.sources" in validate_delivery(delivery).errors


def test_delivery_with_sources_matches_the_canonical_schema() -> None:
    documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "schemas").glob("*.json"))
    ]
    registry = Registry().with_resources(
        [(document["$id"], Resource.from_contents(document)) for document in documents]
    )
    by_id = {document["$id"]: document for document in documents}
    delivery = _delivery(
        _brief(
            "DONE",
            sources=_sources("warehouse.orders: ok", "fx.rates: stale (optional) — 3 days old"),
        )
    )
    for identifier in (
        "https://github.com/luanmorenommaciel/brief-spec/releases/download/v0.5.0/"
        "brief-spec-delivery.schema.json",
    ):
        schema = by_id[identifier]
        validator_for(schema)(schema, registry=registry).validate(delivery)
    for name in ("brief-spec-outcome-brief", "outcome-brief"):
        schema = next(doc for doc in documents if doc["$id"].endswith(f"/{name}.schema.json"))
        assert schema["properties"]["sources"]["items"]["properties"]["status"]["enum"] == [
            status.value for status in SourceStatus
        ]
        assert "sources" not in schema["required"]
