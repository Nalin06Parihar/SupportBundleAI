"""Focused tests for release approval and approved-evidence analysis."""

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.database.models import AnalysisRecord
from app.models.analysis import (
    AnalysisResult,
    ConfidenceLevel,
    EvidenceFinding,
    EvidenceReference,
    EvidenceSufficiency,
)
from app.models.approval import (
    ApprovalStatus,
    EvidenceItemDecision,
    EvidenceItemDecisionType,
    EvidenceReleaseDecision,
    PlanApprovalDecision,
)
from app.models.evidence import (
    MissingEvidence,
    ResidualRiskLevel,
    SanitizedEvidenceExcerpt,
    SensitivityStatus,
)
from app.models.plan import CollectorName, EvidencePlan, EvidenceStep
from app.models.issue import IssueRequest
from app.models.workflow import WorkflowStatus
from app.scenarios.loader import ScenarioLoader
from app.services.analysis_service import (
    build_external_analysis_payload,
    validate_analysis_result,
)
from app.services.workflow_service import WorkflowService


class FakePlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def create_plan(self, issue, scenario) -> EvidencePlan:
        self.calls += 1
        return EvidencePlan(
            issue_summary="Inspect safe synthetic system information.",
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

    def __init__(
        self,
        *,
        sufficiency: EvidenceSufficiency = EvidenceSufficiency.SUFFICIENT,
        supplemental: bool = False,
        invalid_reference: bool = False,
    ) -> None:
        self.calls: list[dict] = []
        self.sufficiency = sufficiency
        self.supplemental = supplemental
        self.invalid_reference = invalid_reference

    async def analyze(self, workflow_id: UUID, payload: dict) -> AnalysisResult:
        self.calls.append(payload)
        excerpt = payload["approved_excerpts"][0]
        artifact_id = UUID(excerpt["artifact_id"])
        excerpt_id = UUID(excerpt["excerpt_id"])
        if self.invalid_reference:
            excerpt_id = uuid4()
        missing = (
            [
                MissingEvidence(
                    collector=CollectorName.RESOURCE_USAGE,
                    reason="No resource usage sample was available.",
                    impact="The issue's resource impact cannot be confirmed.",
                    required=True,
                )
            ]
            if self.supplemental
            else []
        )
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
                            artifact_id=artifact_id,
                            excerpt_id=excerpt_id,
                        )
                    ],
                )
            ],
            missing_evidence=missing,
            evidence_sufficiency=self.sufficiency,
            supplemental_plan_required=self.supplemental,
        )


@pytest.fixture
def analysis_environment(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from pathlib import Path

    controlled = Path(__file__).resolve().parents[1] / "controlled_environment"
    monkeypatch.setenv("CONTROLLED_ENVIRONMENT_DIRECTORY", str(controlled))
    monkeypatch.setenv("OUTPUT_DIRECTORY", str(tmp_path / "output"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _issue(scenario_id: str) -> dict:
    return {
        "title": "Synthetic application diagnostic",
        "description": "Investigate a reported synthetic application issue.",
        "application_name": "MapleDesk",
        "symptoms": ["Degraded response"],
        "scenario_id": scenario_id,
    }


def _service(session_factory, root, analyzer: FakeAnalyzer):
    return WorkflowService(
        session_factory,
        planner=FakePlanner(),
        analyzer=analyzer,
        scenario_loader=ScenarioLoader(),
        checkpoint_path=root / "analysis-checkpoints.sqlite3",
    )


def _approved_release(state) -> EvidenceReleaseDecision:
    excerpt = state.sanitized_excerpts[0]
    return EvidenceReleaseDecision(
        workflow_id=state.workflow_id,
        approval_id=state.active_approval_request.approval_id,
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
    )


def _collect_and_review(service: WorkflowService, scenario_id: str):
    pending = asyncio.run(
        service.start_workflow(IssueRequest(**_issue(scenario_id)))
    )
    plan_decision = PlanApprovalDecision(
        workflow_id=pending.workflow_id,
        approval_id=pending.active_approval_request.approval_id,
        status=ApprovalStatus.APPROVED,
        approved_step_ids=[step.step_id for step in pending.evidence_plan.steps],
    )
    return asyncio.run(service.resume_workflow(pending.workflow_id, plan_decision))


@pytest.mark.parametrize(
    "scenario_id",
    [
        "application-startup-failure",
        "high-cpu-usage",
        "network-dependency-timeout",
    ],
)
def test_approved_sanitized_excerpt_is_analyzed(
    session_factory,
    analysis_environment,
    scenario_id: str,
) -> None:
    analyzer = FakeAnalyzer()
    service = _service(session_factory, analysis_environment, analyzer)
    reviewed = _collect_and_review(service, scenario_id)

    completed = asyncio.run(
        service.resume_evidence_release(
            reviewed.workflow_id,
            _approved_release(reviewed),
        )
    )

    assert completed.current_stage == "request_bundle_release"
    assert completed.status == WorkflowStatus.AWAITING_RELEASE_APPROVAL
    assert completed.analysis_result is not None
    assert len(analyzer.calls) == 1
    assert len(analyzer.calls[0]["approved_excerpts"]) == 1
    assert completed.analysis_result.findings[0].evidence_references[0].excerpt_id == (
        reviewed.sanitized_excerpts[0].excerpt_id
    )


def test_external_payload_omits_excluded_and_blocked_evidence() -> None:
    workflow_id = uuid4()
    included_artifact = uuid4()
    blocked_artifact = uuid4()
    included_excerpt = SanitizedEvidenceExcerpt(
        artifact_id=included_artifact,
        sequence_number=0,
        content="Sanitized approved details.",
        redaction_count=0,
        residual_risk_level=ResidualRiskLevel.NONE,
    )
    excluded_excerpt = SanitizedEvidenceExcerpt(
        artifact_id=blocked_artifact,
        sequence_number=0,
        content="Blocked evidence must not leave local storage.",
        redaction_count=0,
        residual_risk_level=ResidualRiskLevel.BLOCKED,
    )
    decision = EvidenceReleaseDecision(
        workflow_id=workflow_id,
        approval_id=uuid4(),
        status=ApprovalStatus.PARTIALLY_APPROVED,
        item_decisions=[
            EvidenceItemDecision(
                artifact_id=included_artifact,
                decision=EvidenceItemDecisionType.INCLUDE,
                approved_excerpt_ids=[included_excerpt.excerpt_id],
                excluded_excerpt_ids=[],
            ),
            EvidenceItemDecision(
                artifact_id=blocked_artifact,
                decision=EvidenceItemDecisionType.EXCLUDE,
                excluded_excerpt_ids=[excluded_excerpt.excerpt_id],
            ),
        ],
        allow_external_ai_analysis=True,
        reviewer_acknowledgement=True,
    )
    artifacts = [
        SimpleNamespace(
            id=str(included_artifact),
            workflow_id=str(workflow_id),
            sanitized=True,
            sensitivity_status=SensitivityStatus.AWAITING_HUMAN_REVIEW.value,
            artifact_type="application_log",
        ),
        SimpleNamespace(
            id=str(blocked_artifact),
            workflow_id=str(workflow_id),
            sanitized=False,
            sensitivity_status=SensitivityStatus.BLOCKED.value,
            artifact_type="application_log",
        ),
    ]

    payload, _ = build_external_analysis_payload(
        workflow_id=workflow_id,
        issue_summary="Synthetic issue summary.",
        decision=decision,
        excerpts=[included_excerpt, excluded_excerpt],
        artifacts=artifacts,
        source_collectors={str(included_artifact): CollectorName.SYSTEM_INFO.value},
        missing_evidence=[],
        warnings=[],
        maximum_bytes=65_536,
    )

    assert [item["excerpt_id"] for item in payload["approved_excerpts"]] == [
        str(included_excerpt.excerpt_id)
    ]
    assert "Blocked evidence" not in str(payload)


def test_rejected_release_does_not_call_analyzer(
    session_factory,
    analysis_environment,
) -> None:
    analyzer = FakeAnalyzer()
    service = _service(session_factory, analysis_environment, analyzer)
    reviewed = _collect_and_review(service, "high-cpu-usage")
    decision = EvidenceReleaseDecision(
        workflow_id=reviewed.workflow_id,
        approval_id=reviewed.active_approval_request.approval_id,
        status=ApprovalStatus.REJECTED,
        item_decisions=[],
        allow_external_ai_analysis=False,
        reviewer_acknowledgement=True,
    )

    rejected = asyncio.run(
        service.resume_evidence_release(reviewed.workflow_id, decision)
    )

    assert rejected.status == WorkflowStatus.REJECTED
    assert analyzer.calls == []


def test_analysis_rejects_unapproved_excerpt_reference() -> None:
    workflow_id = uuid4()
    artifact_id = uuid4()
    approved_excerpt_id = uuid4()
    result = AnalysisResult(
        workflow_id=workflow_id,
        issue_summary="Synthetic issue.",
        findings=[
            EvidenceFinding(
                title="Unsupported excerpt.",
                description="This reference was not approved.",
                category="system",
                confidence=ConfidenceLevel.LOW,
                evidence_references=[
                    EvidenceReference(
                        artifact_id=artifact_id,
                        excerpt_id=uuid4(),
                    )
                ],
            )
        ],
        evidence_sufficiency=EvidenceSufficiency.PARTIALLY_SUFFICIENT,
        supplemental_plan_required=False,
    )

    with pytest.raises(ValueError, match="unapproved excerpt"):
        validate_analysis_result(
            result,
            workflow_id=workflow_id,
            approved_artifacts={artifact_id},
            approved_excerpts={approved_excerpt_id: artifact_id},
        )


def test_insufficient_analysis_allows_only_one_supplemental_cycle(
    session_factory,
    analysis_environment,
) -> None:
    analyzer = FakeAnalyzer(
        sufficiency=EvidenceSufficiency.INSUFFICIENT,
        supplemental=True,
    )
    service = _service(session_factory, analysis_environment, analyzer)
    reviewed = _collect_and_review(service, "network-dependency-timeout")
    first = asyncio.run(
        service.resume_evidence_release(
            reviewed.workflow_id,
            _approved_release(reviewed),
        )
    )

    assert first.status == WorkflowStatus.AWAITING_SUPPLEMENTAL_APPROVAL
    assert first.supplemental_cycle_count == 1
    supplemental_decision = PlanApprovalDecision(
        workflow_id=first.workflow_id,
        approval_id=first.active_approval_request.approval_id,
        status=ApprovalStatus.APPROVED,
        approved_step_ids=[step.step_id for step in first.evidence_plan.steps],
    )
    reviewed_again = asyncio.run(
        service.resume_workflow(first.workflow_id, supplemental_decision)
    )
    second = asyncio.run(
        service.resume_evidence_release(
            reviewed_again.workflow_id,
            _approved_release(reviewed_again),
        )
    )

    assert second.status == WorkflowStatus.AWAITING_RELEASE_APPROVAL
    assert second.supplemental_cycle_count == 1
    assert len(analyzer.calls) == 2


def test_reusing_release_decision_does_not_duplicate_analysis(
    session_factory,
    analysis_environment,
) -> None:
    analyzer = FakeAnalyzer()
    service = _service(session_factory, analysis_environment, analyzer)
    reviewed = _collect_and_review(service, "application-startup-failure")
    decision = _approved_release(reviewed)
    first = asyncio.run(
        service.resume_evidence_release(reviewed.workflow_id, decision)
    )
    second = asyncio.run(
        service.resume_evidence_release(reviewed.workflow_id, decision)
    )

    with session_factory() as session:
        analyses = session.scalar(
            select(func.count()).select_from(AnalysisRecord)
        )
    assert first.analysis_result.analysis_id == second.analysis_result.analysis_id
    assert len(analyzer.calls) == analyses == 1
