from __future__ import annotations

import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from brief_spec_chronicle.derive import build_snapshot, validate_snapshot
from brief_spec_chronicle.operations import (
    archive_project,
    doctor,
    export_approved_lesson,
    lessons,
    record_decision,
    restore_project,
    review_lesson,
)
from brief_spec_chronicle.rendering import (
    deterministic_zip,
    export_snapshot,
    verify_export,
)
from brief_spec_chronicle.storage import (
    atomic_external_write,
    delete_project,
    ingest_event,
    init_project,
    iter_events,
    project_dir,
    registered_project,
    verify_chain,
)
from jsonschema.validators import validator_for
from referencing import Registry, Resource

TIMES = [f"2026-08-14T12:{minute:02}:00+00:00" for minute in range(20)]
ROOT = Path(__file__).resolve().parents[1]


def _event(
    kind: str,
    headline: str,
    occurred_at: str,
    *,
    task_ref: str | None = None,
    intent_ref: str | None = "intent-1",
    evidence: list[str] | None = None,
    details: dict[str, object] | None = None,
    importance: str = "notable",
) -> dict[str, object]:
    method: dict[str, object] = {
        "method": "converge",
        "phase": "execution",
        "intent_ref": intent_ref,
    }
    if task_ref:
        method["task_ref"] = task_ref
    return {
        "kind": kind,
        "headline": headline,
        "importance": importance,
        "access": "local",
        "occurred_at": occurred_at,
        "method_context": method,
        "entity_refs": [task_ref] if task_ref else [intent_ref],
        "evidence_ids": evidence or [],
        "details": details or {},
    }


@pytest.fixture
def chronicle_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, dict[str, object]]:
    monkeypatch.setenv("BRIEF_SPEC_HOME", str(tmp_path / "state"))
    project = tmp_path / "project"
    project.mkdir()
    before = set(project.iterdir())
    registration = init_project(project, name="Continuity Pilot", now=TIMES[0])
    assert set(project.iterdir()) == before
    if os.name != "nt":
        assert (
            project_dir(registration["project_id"]) / "project.json"
        ).stat().st_mode & 0o777 == 0o600
    return project, registration


def _seed(project: Path) -> None:
    values = [
        _event("INTENT_DECLARED", "Deliver verified human continuity", TIMES[0]),
        _event(
            "TASK_STARTED",
            "Implement the Chronicle ledger",
            TIMES[1],
            task_ref="task-ledger",
            details={"authorization_ref": "auth-1"},
        ),
        _event(
            "BLOCKER_RAISED",
            "A schema decision needs human input",
            TIMES[2],
            task_ref="task-ledger",
            details={"human_action": "Choose the schema identifier"},
            importance="blocking",
        ),
        _event(
            "BLOCKER_CLEARED",
            "The schema identifier was selected",
            TIMES[3],
            task_ref="task-ledger",
        ),
        _event(
            "TASK_COMPLETED",
            "The Chronicle ledger is implemented",
            TIMES[4],
            task_ref="task-ledger",
            evidence=["evidence:test-ledger"],
            details={"next": ["Run the end-to-end journey"]},
        ),
        _event(
            "TASK_ACCEPTED",
            "The Chronicle ledger passed independent acceptance",
            TIMES[5],
            task_ref="task-ledger",
            evidence=["evidence:acceptance-receipt"],
        ),
        _event(
            "DECISION_REQUESTED",
            "Choose the first real-project pilot",
            TIMES[6],
            details={
                "decision_id": "decision-pilot",
                "question": "Which project should pilot Chronicle?",
                "alternatives": ["Brief-Spec", "Seamwise"],
                "human_action": "Select one pilot",
            },
            importance="needs-attention",
        ),
        _event(
            "LESSON_PROPOSED",
            "Material events are more useful than raw activity",
            TIMES[7],
            evidence=["evidence:pilot-observation"],
            details={
                "lesson_id": "lesson-material-events",
                "observation": "Retain material transitions, not raw activity",
                "applicability": "cross-project",
                "confidence": "medium",
                "destination": "nexo",
            },
        ),
    ]
    for value in values:
        ingest_event(project, value, source_system="test", observed_at=value["occurred_at"])


def test_project_lifecycle_snapshot_and_deterministic_exports(
    chronicle_project: tuple[Path, dict[str, object]], tmp_path: Path
) -> None:
    project, registration = chronicle_project
    _seed(project)
    events = list(iter_events(registration["project_id"]))
    assert len(events) == 8
    assert verify_chain(events) == []
    duplicate = ingest_event(
        project,
        _event(
            "TASK_ACCEPTED",
            "The Chronicle ledger passed independent acceptance",
            TIMES[5],
            task_ref="task-ledger",
            evidence=["evidence:acceptance-receipt"],
        ),
        source_system="test",
        observed_at=TIMES[5],
    )
    assert duplicate["status"] == "DUPLICATE"
    assert len(list(iter_events(registration["project_id"]))) == 8

    snapshot = build_snapshot(project, created_at=TIMES[8])
    assert validate_snapshot(snapshot) == []
    assert (
        snapshot["current_state"]["headline"] == "Material events are more useful than raw activity"
    )
    assert snapshot["milestones"][0]["evidence_ids"] == ["evidence:test-ledger"]
    assert any(item["relation"] == "implements" for item in snapshot["relations"])
    assert snapshot["drift"] == []
    assert snapshot["decisions"][0]["state"] == "requested"

    first = tmp_path / "first"
    second = tmp_path / "second"
    export_snapshot(snapshot, first, formats={"markdown", "json", "html", "zip"})
    export_snapshot(snapshot, second, formats={"markdown", "json", "html", "zip"})
    html = (first / "chronicle.html").read_text(encoding="utf-8")
    assert "overflow-wrap: anywhere; word-break: break-word" in html
    assert "overflow-wrap: anywhere; word-break: break-all" in html
    for name in (
        "chronicle.md",
        "chronicle.json",
        "chronicle.html",
        "chronicle.zip",
        "manifest.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    verification = verify_export(first, level="rendered")
    assert verification["status"] == "WARN"
    assert any("Opaque evidence ID" in warning for warning in verification["warnings"])
    assert verify_export(first / "chronicle.zip", level="structural")["status"] == "PASS"
    assert verify_export(first / "chronicle.md", level="structural")["status"] == "PASS"
    assert verify_export(first / "chronicle-receipt.json", level="structural")["status"] == "PASS"
    receipt = json.loads((first / "chronicle-receipt.json").read_text())
    assert receipt["snapshot_sha256"] == snapshot["canonical_sha256"]

    single = tmp_path / "single-presentation"
    export_snapshot(snapshot, single, formats={"markdown"})
    assert (single / "chronicle.md").is_file()
    assert (single / "chronicle.json").is_file()
    assert not (single / "chronicle.html").exists()
    assert verify_export(single / "chronicle.md", level="structural")["status"] == "PASS"

    first_archive = tmp_path / "first.zip"
    second_archive = tmp_path / "second.zip"
    archive_project(project, first_archive)
    archive_project(project, second_archive)
    assert first_archive.read_bytes() == second_archive.read_bytes()
    assert doctor(project)["status"] == "PASS"


def test_generated_contracts_pass_independent_json_schema_validation(
    chronicle_project: tuple[Path, dict[str, object]], tmp_path: Path
) -> None:
    project, registration = chronicle_project
    _seed(project)
    snapshot = build_snapshot(project, created_at=TIMES[8])
    schema_root = ROOT / "packages" / "brief-spec-chronicle" / "schemas"
    registry = Registry()
    schemas: dict[str, dict[str, object]] = {}
    for path in schema_root.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        registry = registry.with_resource(str(schema["$id"]), Resource.from_contents(schema))
    chronicle_schema = schemas["brief-spec-chronicle.schema.json"]
    validator = validator_for(chronicle_schema)(chronicle_schema, registry=registry)
    assert list(validator.iter_errors(snapshot)) == []
    event_schema = json.loads((ROOT / "schemas" / "brief-spec-event.schema.json").read_text())
    event_validator = validator_for(event_schema)(event_schema)
    for event in iter_events(registration["project_id"]):
        assert list(event_validator.iter_errors(event)) == []
    output = tmp_path / "schema-output"
    export_snapshot(snapshot, output, formats={"json"})
    receipt = json.loads((output / "chronicle-receipt.json").read_text())
    receipt_schema = schemas["brief-spec-chronicle-receipt.schema.json"]
    receipt_validator = validator_for(receipt_schema)(receipt_schema, registry=registry)
    assert list(receipt_validator.iter_errors(receipt)) == []


def test_resolved_evidence_receipt_tamper_and_transactional_export(
    chronicle_project: tuple[Path, dict[str, object]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = chronicle_project
    proof = project / "proof.txt"
    proof.write_text("verified\n", encoding="utf-8")
    from briefspec.artifacts import sha256_bytes

    ingest_event(
        project,
        _event(
            "TASK_COMPLETED",
            "A locally resolvable result",
            TIMES[1],
            task_ref="task-proof",
            evidence=[f"file:proof.txt#sha256={sha256_bytes(proof.read_bytes())}"],
            details={"authorization_ref": "auth-proof"},
        ),
        source_system="test",
        observed_at=TIMES[1],
    )
    snapshot = build_snapshot(project, created_at=TIMES[2])
    output = tmp_path / "resolved"
    export_snapshot(snapshot, output, formats={"markdown", "json", "html", "zip"})
    assert verify_export(output, level="resolved", workspace=project)["status"] == "PASS"
    (output / "chronicle.md").write_text("tampered", encoding="utf-8")
    assert verify_export(output, level="structural")["status"] == "FAIL"

    import sys
    from types import SimpleNamespace

    failed = tmp_path / "failed"

    def fail_renderer(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("renderer failed")

    monkeypatch.setitem(
        sys.modules,
        "briefspec_renderer_pdf",
        SimpleNamespace(render_html_document=fail_renderer),
    )
    with pytest.raises(RuntimeError, match="renderer failed"):
        export_snapshot(snapshot, failed, formats={"markdown", "pdf"})
    assert not failed.exists()


def test_archive_restore_to_new_project_root(
    chronicle_project: tuple[Path, dict[str, object]], tmp_path: Path
) -> None:
    project, registration = chronicle_project
    _seed(project)
    archive = tmp_path / "chronicle-archive.zip"
    archive_project(project, archive)
    delete_project(registration["project_id"], registration["project_id"])
    restored_root = tmp_path / "restored-project"
    restored_root.mkdir()
    restored = restore_project(archive, restored_root)
    assert restored["status"] == "RESTORED"
    assert registered_project(restored_root)["project_id"] == registration["project_id"]
    assert len(list(iter_events(registration["project_id"]))) == 8
    assert doctor(restored_root)["status"] == "PASS"


def test_drift_pivot_decisions_lessons_and_delete(
    chronicle_project: tuple[Path, dict[str, object]], tmp_path: Path
) -> None:
    project, registration = chronicle_project
    ingest_event(
        project,
        _event(
            "TASK_STARTED",
            "Start without a Converge authorization",
            TIMES[1],
            task_ref="task-risk",
        ),
        source_system="converge",
        observed_at=TIMES[1],
    )
    ingest_event(
        project,
        _event(
            "TASK_COMPLETED",
            "Complete without evidence",
            TIMES[2],
            task_ref="task-risk",
        ),
        source_system="converge",
        observed_at=TIMES[2],
    )
    first = build_snapshot(project, created_at=TIMES[3])
    assert {item["category"] for item in first["drift"]} == {
        "acceptance",
        "authority",
        "evidence",
    }
    drift_ids = [item["drift_id"] for item in first["drift"]]
    record_decision(
        project,
        {
            "decision_id": "decision-new-baseline",
            "question": "Approve the revised execution baseline?",
            "choice": "approved",
            "owner": "human",
            "rationale": "The revised scope is intentional",
        },
        observed_at=TIMES[3],
    )
    ingest_event(
        project,
        _event(
            "INTENT_REVISED",
            "Human approved a new execution baseline",
            TIMES[4],
            details={
                "decision_id": "decision-new-baseline",
                "supersedes": "intent-1",
                "resolves_drift_ids": drift_ids,
            },
        ),
        source_system="human",
        observed_at=TIMES[4],
    )
    pivoted = build_snapshot(project, created_at=TIMES[5])
    assert {item["disposition"] for item in pivoted["drift"]} == {"superseded-by-pivot"}

    ingest_event(
        project,
        _event(
            "LESSON_PROPOSED",
            "Require authorization before execution",
            TIMES[6],
            evidence=["evidence:drift-fixture"],
            details={
                "lesson_id": "lesson-auth",
                "observation": "Require an authorization reference before execution",
                "confidence": "high",
            },
        ),
        source_system="test",
        observed_at=TIMES[6],
    )
    review_lesson(
        project,
        "lesson-auth",
        choice="approved",
        reason="The evidence supports a proposal",
        observed_at=TIMES[7],
    )
    assert lessons(project)[0]["review_state"] == "approved"
    envelope = tmp_path / "lesson.json"
    exported = export_approved_lesson(project, "lesson-auth", envelope)
    assert exported["proposal"]["canonical_knowledge"] is False
    assert json.loads(envelope.read_text())["sha256"] == exported["sha256"]

    ingest_event(
        project,
        _event(
            "LESSON_PROPOSED",
            "A rejected lesson stays local",
            TIMES[9],
            evidence=["evidence:rejected-lesson"],
            details={
                "lesson_id": "lesson-rejected",
                "observation": "Do not promote this proposal",
                "confidence": "low",
            },
        ),
        source_system="test",
        observed_at=TIMES[9],
    )
    review_lesson(
        project,
        "lesson-rejected",
        choice="rejected",
        reason="The evidence is insufficient",
        observed_at=TIMES[10],
    )
    with pytest.raises(ValueError, match="approval receipt"):
        export_approved_lesson(project, "lesson-rejected", tmp_path / "rejected.json")

    decision = record_decision(
        project,
        {
            "decision_id": "decision-method",
            "question": "Use deterministic drift?",
            "choice": "yes",
            "owner": "human",
            "rationale": "Avoid hidden model claims",
        },
        observed_at=TIMES[8],
    )
    assert decision["status"] == "INGESTED"
    with pytest.raises(ValueError, match="exactly match"):
        delete_project(registration["project_id"], "wrong")
    result = delete_project(registration["project_id"], registration["project_id"])
    assert result["status"] == "DELETED"
    assert result["recoverable"] is False
    assert not project_dir(registration["project_id"]).exists()


def test_rejected_input_never_reaches_the_ledger(
    chronicle_project: tuple[Path, dict[str, object]],
) -> None:
    project, registration = chronicle_project
    with pytest.raises(ValueError, match="forbidden"):
        ingest_event(
            project,
            _event(
                "HUMAN_NOTE",
                "Unsafe event",
                TIMES[0],
                details={"transcript": "must not persist"},
            ),
            source_system="test",
            observed_at=TIMES[0],
        )
    assert list(iter_events(registration["project_id"])) == []
    assert registered_project(project)["project_id"] == registration["project_id"]


def test_archive_traversal_is_rejected_before_restore(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape", b"unsafe")
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError, match="Unsafe Chronicle archive member"):
        restore_project(archive, project)
    verified = verify_export(archive, level="structural")
    assert verified["status"] == "FAIL"
    assert any("Unsafe Chronicle ZIP member" in error for error in verified["errors"])


def test_doctor_rejects_foreign_symlinks_without_touching_the_target(
    chronicle_project: tuple[Path, dict[str, object]], tmp_path: Path
) -> None:
    project, registration = chronicle_project
    outside = tmp_path / "outside.txt"
    outside.write_text("foreign", encoding="utf-8")
    link = project_dir(registration["project_id"]) / "foreign-link"
    link.symlink_to(outside)
    result = doctor(project, fix=True)
    assert result["status"] == "FAIL"
    assert outside.read_text(encoding="utf-8") == "foreign"


def test_failed_index_update_rolls_back_the_appended_event(
    chronicle_project: tuple[Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, registration = chronicle_project
    import brief_spec_chronicle.storage as storage

    def fail_rebuild(_project_id: str) -> dict[str, int]:
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(storage, "rebuild_index", fail_rebuild)
    with pytest.raises(RuntimeError, match="index unavailable"):
        ingest_event(
            project,
            _event("INTENT_DECLARED", "This append must roll back", TIMES[0]),
            source_system="test",
            observed_at=TIMES[0],
        )
    assert list(iter_events(registration["project_id"])) == []
    receipts = project_dir(registration["project_id"]) / "receipts"
    assert not receipts.exists() or not list(receipts.iterdir())


def test_hash_chain_preserves_ingest_order_across_out_of_order_month_segments(
    chronicle_project: tuple[Path, dict[str, object]],
    tmp_path: Path,
) -> None:
    project, registration = chronicle_project
    first = _event(
        "INTENT_DECLARED",
        "Observed in August",
        "2026-08-14T12:00:00+00:00",
    )
    late = _event(
        "PLAN_CREATED",
        "A June occurrence entered a July segment after the August event",
        "2026-06-01T12:00:00+00:00",
    )
    ingest_event(
        project,
        first,
        source_system="test",
        observed_at="2026-08-14T12:00:00+00:00",
    )
    cutoff = list(iter_events(registration["project_id"]))[-1]["event_id"]
    before = build_snapshot(project, ledger_cutoff=cutoff)
    ingest_event(
        project,
        late,
        source_system="test",
        observed_at="2026-07-01T12:00:00+00:00",
    )
    events = list(iter_events(registration["project_id"]))
    assert [event["headline"] for event in events] == [first["headline"], late["headline"]]
    assert verify_chain(events) == []
    replay = build_snapshot(project, ledger_cutoff=cutoff)
    assert replay == before
    snapshot = build_snapshot(project, created_at=TIMES[2])
    assert [item["headline"] for item in snapshot["material_changes"]] == [
        late["headline"],
        first["headline"],
    ]
    assert snapshot["material_changes"][0]["late_arrival"] is True
    archive = tmp_path / "out-of-order-archive.zip"
    archive_project(project, archive)
    assert verify_export(archive, level="structural")["status"] == "PASS"


def test_cross_harness_correlation_deduplicates_one_material_transition(
    chronicle_project: tuple[Path, dict[str, object]],
) -> None:
    project, registration = chronicle_project
    event = _event(
        "TASK_ACCEPTED",
        "One accepted result observed by multiple harnesses",
        TIMES[1],
        task_ref="task-shared",
        evidence=["evidence:acceptance"],
        details={"correlation_id": "acceptance:task-shared:revision-1"},
    )
    first = ingest_event(
        project,
        event,
        source_system="codex",
        observed_at=TIMES[1],
    )
    second = ingest_event(
        project,
        {**event, "source": {"harness": "claude"}},
        source_system="claude",
        observed_at=TIMES[2],
    )
    assert first["status"] == "INGESTED"
    assert second["status"] == "DUPLICATE"
    assert len(list(iter_events(registration["project_id"]))) == 1


def test_private_and_expired_evidence_remain_visible_without_network_access(
    chronicle_project: tuple[Path, dict[str, object]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = chronicle_project
    for index, value in enumerate(
        (
            {
                "kind": "EVIDENCE_ADDED",
                "importance": "notable",
                "access": "public",
                "occurred_at": TIMES[0],
                "headline": "A public artifact expired",
                "method_context": {"method": "general"},
                "entity_refs": ["task-evidence"],
                "evidence_ids": ["https://example.com/expired"],
                "details": {"expires_at": TIMES[1]},
            },
            {
                "kind": "EVIDENCE_ADDED",
                "importance": "notable",
                "access": "private",
                "occurred_at": TIMES[2],
                "headline": "A private artifact requires authorized review",
                "method_context": {"method": "general"},
                "entity_refs": ["task-evidence"],
                "evidence_ids": ["https://private.example.com/artifact"],
                "details": {"expires_at": TIMES[9]},
            },
        )
    ):
        ingest_event(
            project,
            value,
            source_system="test",
            observed_at=TIMES[index * 2],
        )
    snapshot = build_snapshot(project, created_at=TIMES[5])
    output = tmp_path / "classified-evidence"
    export_snapshot(snapshot, output, formats={"markdown", "json", "html"})
    markdown = (output / "chronicle.md").read_text(encoding="utf-8")
    assert "access: `private`" in markdown
    assert TIMES[1] in markdown

    def network_must_not_run(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("private or expired evidence must not trigger network access")

    import brief_spec_chronicle.rendering as rendering

    monkeypatch.setattr(rendering, "resolve_public_url", network_must_not_run)
    verified = verify_export(output, level="resolved", workspace=project)
    assert verified["status"] == "FAIL"
    assert any("expired" in error for error in verified["errors"])
    assert any("Private evidence URL" in warning for warning in verified["warnings"])


def test_historical_report_marks_newer_material_events_as_stale(
    chronicle_project: tuple[Path, dict[str, object]],
) -> None:
    project, _ = chronicle_project
    ingest_event(
        project,
        _event("INTENT_DECLARED", "Initial intent", TIMES[0]),
        source_system="test",
        observed_at=TIMES[0],
    )
    ingest_event(
        project,
        _event("PLAN_CREATED", "A newer plan exists", TIMES[2]),
        source_system="test",
        observed_at=TIMES[2],
    )
    snapshot = build_snapshot(project, until=TIMES[0], created_at=TIMES[3])
    stale = [item for item in snapshot["drift"] if item["category"] == "stale-report"]
    assert len(stale) == 1
    assert stale[0]["source_event_ids"]


def test_failure_repair_and_acceptance_settle_without_residual_drift(
    chronicle_project: tuple[Path, dict[str, object]],
) -> None:
    project, _ = chronicle_project
    values = (
        _event("TASK_FAILED", "The first attempt failed", TIMES[0], task_ref="task-repair"),
        _event(
            "TASK_STARTED",
            "Authorized repair began",
            TIMES[1],
            task_ref="task-repair",
            details={"authorization_ref": "auth-repair"},
        ),
        _event(
            "TASK_COMPLETED",
            "The repair completed with proof",
            TIMES[2],
            task_ref="task-repair",
            evidence=["evidence:repair-test"],
        ),
        _event(
            "TASK_ACCEPTED",
            "The repaired task was accepted",
            TIMES[3],
            task_ref="task-repair",
            evidence=["evidence:repair-acceptance"],
        ),
    )
    for value in values:
        ingest_event(
            project,
            value,
            source_system="converge",
            observed_at=str(value["occurred_at"]),
        )
    snapshot = build_snapshot(project, created_at=TIMES[4])
    assert snapshot["drift"] == []
    assert any(item["kind"] == "TASK_FAILED" for item in snapshot["material_changes"])


def test_concurrent_ingestion_serializes_one_valid_hash_chain(
    chronicle_project: tuple[Path, dict[str, object]],
) -> None:
    project, registration = chronicle_project

    def ingest(index: int) -> str:
        result = ingest_event(
            project,
            _event(
                "HUMAN_NOTE",
                f"Concurrent material note {index}",
                TIMES[index],
                details={"correlation_id": f"concurrent:{index}"},
            ),
            source_system="test",
            observed_at=TIMES[index],
        )
        return str(result["status"])

    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(ingest, range(10)))
    assert statuses == ["INGESTED"] * 10
    events = list(iter_events(registration["project_id"]))
    assert len(events) == 10
    assert verify_chain(events) == []


def test_doctor_rebuilds_a_missing_derived_index(
    chronicle_project: tuple[Path, dict[str, object]],
) -> None:
    project, registration = chronicle_project
    _seed(project)
    index = project_dir(registration["project_id"]) / "index.sqlite3"
    index.unlink()
    project_record = project_dir(registration["project_id"]) / "project.json"
    project_record.chmod(0o644)
    assert doctor(project)["status"] == "WARN"
    repaired = doctor(project, fix=True)
    assert repaired["status"] == "PASS"
    assert repaired["fix"]["events"] == 8
    assert index.is_file()
    if os.name != "nt":
        assert project_record.stat().st_mode & 0o777 == 0o600


def test_external_write_and_zip_do_not_depend_on_unix_only_apis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """os.fchmod is Unix-only, and Windows cannot reopen an open NamedTemporaryFile
    by name. Neither export path may rely on those behaviors."""
    monkeypatch.delattr(os, "fchmod", raising=False)
    target = tmp_path / "out" / "lesson.json"
    atomic_external_write(target, b'{"lesson": "portable"}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"lesson": "portable"}
    assert list(target.parent.glob(".lesson.json.*")) == []

    created_at = "2026-08-14T12:01:00+00:00"
    payload = {"b.txt": b"second", "a.txt": b"first"}
    archive = deterministic_zip(payload, created_at)
    assert archive == deterministic_zip(payload, created_at)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.namelist() == ["a.txt", "b.txt"]
        assert bundle.read("b.txt") == b"second"
