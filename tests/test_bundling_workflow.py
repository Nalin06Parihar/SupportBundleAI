"""Demo-ready report, bundle verification, and release-approval tests."""

import asyncio
import json
import zipfile
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.bundling.exporter import create_candidate_zip, verify_candidate_zip
from app.bundling.manifest import generate_manifest
from app.core.config import get_settings
from app.database.models import BundleRecord
from app.models.analysis import (
    AnalysisResult,
    ConfidenceLevel,
    EvidenceFinding,
    EvidenceReference,
    EvidenceSufficiency,
)
from app.models.approval import (
    ApprovalStatus,
    BundleReleaseDecision,
    EvidenceItemDecision,
    EvidenceItemDecisionType,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)
from app.models.bundle import ManifestEntry
from app.models.evidence import ArtifactType
from app.models.issue import IssueRequest
from app.models.plan import CollectorName, EvidencePlan, EvidenceStep
from app.models.workflow import WorkflowStatus
from app.scenarios.loader import ScenarioLoader
from app.services.workflow_service import WorkflowService


class FakePlanner:
    async def create_plan(self, issue, scenario) -> EvidencePlan:
        return EvidencePlan(
            issue_summary="Review synthetic system information.",
            suspected_category="system",
            steps=[
                EvidenceStep(
                    collector=CollectorName.SYSTEM_INFO,
                    reason="Review synthetic system details relevant to the issue.",
                )
            ],
        )


class FakeAnalyzer:
    maximum_payload_bytes = 65_536

    async def analyze(self, workflow_id: UUID, payload: dict) -> AnalysisResult:
        excerpt = payload["approved_excerpts"][0]
        return AnalysisResult(
            workflow_id=workflow_id,
            issue_summary=payload["issue_summary"],
            findings=[
                EvidenceFinding(
                    title="Synthetic system details are available.",
                    description="The approved excerpt contains system information.",
                    category="system",
                    confidence=ConfidenceLevel.MEDIUM,
                    evidence_references=[
                        EvidenceReference(
                            artifact_id=UUID(excerpt["artifact_id"]),
                            excerpt_id=UUID(excerpt["excerpt_id"]),
                        )
                    ],
                )
            ],
            evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
            supplemental_plan_required=False,
        )


@pytest.fixture
def demo_environment(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from pathlib import Path

    controlled = Path(__file__).resolve().parents[1] / "controlled_environment"
    monkeypatch.setenv("CONTROLLED_ENVIRONMENT_DIRECTORY", str(controlled))
    monkeypatch.setenv("OUTPUT_DIRECTORY", str(tmp_path / "output"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _service(session_factory, root) -> WorkflowService:
    return WorkflowService(
        session_factory,
        planner=FakePlanner(),
        analyzer=FakeAnalyzer(),
        scenario_loader=ScenarioLoader(),
        checkpoint_path=root / "workflow-checkpoints.sqlite3",
    )


async def _reach_release(service: WorkflowService, scenario_id: str):
    pending = await service.start_workflow(
        IssueRequest(
            title="<img src=x onerror=alert(1)> synthetic incident",
            description="Investigate a synthetic application issue.",
            application_name="MapleDesk",
            symptoms=["Degraded response"],
            scenario_id=scenario_id,
        )
    )
    collected = await service.resume_workflow(
        pending.workflow_id,
        PlanApprovalDecision(
            workflow_id=pending.workflow_id,
            approval_id=pending.active_approval_request.approval_id,
            status=ApprovalStatus.APPROVED,
            approved_step_ids=[step.step_id for step in pending.evidence_plan.steps],
        ),
    )
    excerpt = collected.sanitized_excerpts[0]
    released = await service.resume_evidence_release(
        collected.workflow_id,
        EvidenceReleaseDecision(
            workflow_id=collected.workflow_id,
            approval_id=collected.active_approval_request.approval_id,
            status=ApprovalStatus.APPROVED,
            item_decisions=[
                EvidenceItemDecision(
                    artifact_id=excerpt.artifact_id,
                    decision=EvidenceItemDecisionType.INCLUDE,
                    approved_excerpt_ids=[excerpt.excerpt_id],
                )
            ],
            allow_external_ai_analysis=True,
            reviewer_acknowledgement=True,
        ),
    )
    return released


def _bundle_decision(state, status: ApprovalStatus) -> BundleReleaseDecision:
    return BundleReleaseDecision(
        workflow_id=state.workflow_id,
        approval_id=state.active_approval_request.approval_id,
        bundle_id=state.bundle_metadata.bundle_id,
        status=status,
        reviewer_acknowledgement=status == ApprovalStatus.APPROVED,
    )


@pytest.mark.parametrize(
    "scenario_id",
    [
        "application-startup-failure",
        "high-cpu-usage",
        "network-dependency-timeout",
    ],
)
def test_shared_workflow_builds_verified_bundle_for_each_scenario(
    session_factory,
    demo_environment,
    scenario_id: str,
) -> None:
    service = _service(session_factory, demo_environment)
    pending = asyncio.run(_reach_release(service, scenario_id))

    assert pending.status == WorkflowStatus.AWAITING_RELEASE_APPROVAL
    assert pending.bundle_metadata.verification_status.value == "verified"
    assert pending.bundle_metadata.sanitized_only
    assert pending.bundle_review_payload["manifest_verification"] == "verified"
    assert "approval_id" not in pending.bundle_review_payload
    assert all(
        "content" not in item
        for item in pending.bundle_review_payload.values()
        if isinstance(item, dict)
    )
    root = demo_environment / "output"
    archive_path = root / pending.bundle_metadata.relative_path
    candidate = root / pending.bundle_metadata.manifest_relative_path
    candidate_root = candidate.parent
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert "escalation_summary.html" in names
        assert {
            "issue.json",
            "evidence_plan.json",
            "collection_results.json",
            "sanitization_report.json",
            "analysis_result.json",
            "missing_evidence.json",
            "manifest.json",
        }.issubset(names)
        html = archive.read("escalation_summary.html").decode("utf-8")
        assert "&lt;img" in html
        assert "<img" not in html
        manifest = [
            ManifestEntry.model_validate(item)
            for item in json.loads(archive.read("manifest.json"))
        ]
        artifact_members = {name for name in names if name.startswith("artifacts/")}
        assert artifact_members
        assert len(manifest) == len(names) - 1
        assert {entry.relative_path for entry in manifest} | {"manifest.json"} == names


@pytest.mark.parametrize("tamper", ["modified", "missing", "unsafe", "unapproved_artifact"])
def test_zip_verification_rejects_tampered_or_unapproved_members(
    tmp_path,
    tamper: str,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "summary.html").write_text("<p>safe</p>", encoding="utf-8")
    file_types = {"summary.html": ArtifactType.OTHER}
    artifact_paths: set[str] = set()
    if tamper == "unapproved_artifact":
        artifact_name = f"artifacts/{uuid4()}.log"
        artifact = candidate / artifact_name
        artifact.parent.mkdir()
        artifact.write_text("unexpected", encoding="utf-8")
        file_types[artifact_name] = ArtifactType.APPLICATION_LOG
    entries = generate_manifest(candidate, file_types)
    archive = tmp_path / "candidate.zip"
    create_candidate_zip(candidate, archive)
    if tamper == "modified":
        (candidate / "summary.html").write_text("<p>changed</p>", encoding="utf-8")
        create_candidate_zip(candidate, archive)
    elif tamper == "missing":
        (candidate / "summary.html").unlink()
        create_candidate_zip(candidate, archive)
    elif tamper == "unsafe":
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("../escape.txt", "unsafe")

    expected_artifacts = (
        set(file_types) - {"summary.html"}
        if tamper != "unapproved_artifact"
        else artifact_paths
    )
    with pytest.raises((ValueError, KeyError, zipfile.BadZipFile)):
        verify_candidate_zip(
            candidate,
            archive,
            entries,
            required_files={"summary.html"},
            approved_artifacts=expected_artifacts,
        )


@pytest.mark.parametrize(
    ("decision_status", "expected_status"),
    [
        (ApprovalStatus.APPROVED, WorkflowStatus.COMPLETED),
        (ApprovalStatus.REJECTED, WorkflowStatus.REJECTED),
    ],
)
def test_bundle_approval_releases_only_when_approved_and_is_idempotent(
    session_factory,
    demo_environment,
    decision_status: ApprovalStatus,
    expected_status: WorkflowStatus,
) -> None:
    service = _service(session_factory, demo_environment)
    pending = asyncio.run(_reach_release(service, "high-cpu-usage"))
    decision = _bundle_decision(pending, decision_status)
    finalized = asyncio.run(
        service.resume_bundle_release(pending.workflow_id, decision)
    )
    events_after_first = len(finalized.events)
    repeated = asyncio.run(
        service.resume_bundle_release(pending.workflow_id, decision)
    )
    bundle = next(
        item
        for item in service._bundles.list_for_workflow(pending.workflow_id)
        if item.id == str(pending.bundle_metadata.bundle_id)
    )

    assert finalized.status == expected_status
    assert repeated.status == expected_status
    assert len(repeated.events) == events_after_first
    assert (bundle.release_status == ApprovalStatus.APPROVED.value) == (
        decision_status == ApprovalStatus.APPROVED
    )
    with session_factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(BundleRecord)
            .where(BundleRecord.workflow_id == str(pending.workflow_id))
        ) == 1
