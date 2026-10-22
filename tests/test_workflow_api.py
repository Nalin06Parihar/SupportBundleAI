"""HTTP tests for the minimal workflow review API."""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.database.base import Base
from app.main import app
from app.models.approval import ApprovalStatus
from app.models.analysis import (
    AnalysisResult,
    ConfidenceLevel,
    EvidenceFinding,
    EvidenceReference,
    EvidenceSufficiency,
)
from app.models.plan import CollectorName, EvidencePlan, EvidenceStep
from app.repositories.approvals import ApprovalRepository
from app.repositories.workflows import WorkflowRepository
from app.models.workflow import WorkflowStatus
from app.api.workflows import get_workflow_service
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
def workflow_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    controlled = Path(__file__).resolve().parents[1] / "controlled_environment"
    monkeypatch.setenv("CONTROLLED_ENVIRONMENT_DIRECTORY", str(controlled))
    monkeypatch.setenv("OUTPUT_DIRECTORY", str(tmp_path / "output"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    monkeypatch.setenv("GROQ_API_KEY", "test-placeholder-key")
    monkeypatch.setenv("GROQ_MODEL", "test-placeholder-model")
    get_settings.cache_clear()
    service: WorkflowService | None = None

    def override_service():
        nonlocal service
        if service is None:
            service = WorkflowService(
                app.state.session_factory,
                planner=FakePlanner(),
                analyzer=FakeAnalyzer(),
                settings=get_settings(),
            )
        return service

    app.dependency_overrides[get_workflow_service] = override_service
    try:
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.db_engine)
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def _issue(scenario_id: str) -> dict[str, object]:
    return {
        "title": "Synthetic application is degraded",
        "description": "Investigate a synthetic application issue.",
        "application_name": "MapleDesk",
        "symptoms": ["Degraded response"],
        "scenario_id": scenario_id,
    }


def _start_and_approve_plan(client: TestClient, scenario_id: str):
    started = client.post("/api/workflows", json=_issue(scenario_id))
    assert started.status_code == 201, started.text
    initial = started.json()
    workflow_id = initial["workflow_id"]
    approval = initial["pending_approval"]
    plan = client.get(f"/api/workflows/{workflow_id}").json()["proposed_plan"]
    decision = {
        "workflow_id": workflow_id,
        "approval_id": approval["approval_id"],
        "status": "approved",
        "approved_step_ids": [step["step_id"] for step in plan["steps"]],
        "rejected_step_ids": [],
    }
    resumed = client.post(
        f"/api/workflows/{workflow_id}/approvals/plan",
        json=decision,
    )
    assert resumed.status_code == 200, resumed.text
    return workflow_id, resumed.json()


def _approve_evidence(client: TestClient, workflow_id: str, state: dict):
    request = state["pending_approval"]
    excerpt = state["evidence_review_excerpts"][0]
    decision = {
        "workflow_id": workflow_id,
        "approval_id": request["approval_id"],
        "status": "approved",
        "item_decisions": [
            {
                "artifact_id": excerpt["artifact_id"],
                "decision": "include",
                "approved_excerpt_ids": [excerpt["excerpt_id"]],
                "excluded_excerpt_ids": [],
            }
        ],
        "allow_external_ai_analysis": True,
        "reviewer_acknowledgement": True,
    }
    return client.post(
        f"/api/workflows/{workflow_id}/approvals/evidence",
        json=decision,
    )


@pytest.mark.parametrize(
    "scenario_id",
    [
        "application-startup-failure",
        "high-cpu-usage",
        "network-dependency-timeout",
    ],
)
def test_scenarios_and_workflow_start_reach_plan_approval(
    workflow_client: TestClient,
    scenario_id: str,
) -> None:
    scenarios = workflow_client.get("/api/scenarios")
    assert scenarios.status_code == 200
    assert len(scenarios.json()) == 3
    assert all("evidence_files" not in item for item in scenarios.json())

    started = workflow_client.post("/api/workflows", json=_issue(scenario_id))
    assert started.status_code == 201, started.text
    payload = started.json()
    assert payload["status"] == WorkflowStatus.AWAITING_PLAN_APPROVAL.value
    assert payload["pending_approval"]["approval_type"] == "collection_plan"


def test_workflow_resumes_through_plan_and_evidence_approval(
    workflow_client: TestClient,
) -> None:
    workflow_id, reviewed = _start_and_approve_plan(
        workflow_client,
        "application-startup-failure",
    )
    assert reviewed["status"] == WorkflowStatus.AWAITING_EVIDENCE_APPROVAL.value
    assert reviewed["evidence_review_excerpts"]

    analyzed = _approve_evidence(workflow_client, workflow_id, reviewed)
    assert analyzed.status_code == 200, analyzed.text
    state = analyzed.json()
    assert state["status"] == WorkflowStatus.AWAITING_RELEASE_APPROVAL.value
    assert state["analysis_result"]["findings"]
    assert "safe_excerpt" not in analyzed.text


def test_rejected_evidence_decision_is_persisted_without_release_validation(
    workflow_client: TestClient,
) -> None:
    workflow_id, reviewed = _start_and_approve_plan(
        workflow_client,
        "application-startup-failure",
    )
    approval = reviewed["pending_approval"]
    decision = {
        "workflow_id": workflow_id,
        "approval_id": approval["approval_id"],
        "status": "rejected",
        "item_decisions": [
            {
                "artifact_id": str(UUID(int=0)),
                "decision": "include",
                "approved_excerpt_ids": [str(UUID(int=1))],
            }
        ],
        "allow_external_ai_analysis": False,
        "reviewer_acknowledgement": False,
    }

    response = workflow_client.post(
        f"/api/workflows/{workflow_id}/approvals/evidence",
        json=decision,
    )

    assert response.status_code == 200, response.text
    state = response.json()
    assert state["status"] == WorkflowStatus.REJECTED.value
    assert state["pending_approval"] is None

    session_factory = app.state.session_factory
    approval_record = ApprovalRepository(session_factory).get(UUID(approval["approval_id"]))
    assert approval_record.status == ApprovalStatus.REJECTED.value
    assert approval_record.decision["status"] == ApprovalStatus.REJECTED.value
    assert any(
        event.event_type == "approval_received"
        and "finalized as rejected" in event.message
        for event in WorkflowRepository(session_factory).list_events(UUID(workflow_id))
    )


def test_bundle_download_requires_final_release_approval(
    workflow_client: TestClient,
) -> None:
    workflow_id, reviewed = _start_and_approve_plan(
        workflow_client,
        "network-dependency-timeout",
    )
    analyzed = _approve_evidence(workflow_client, workflow_id, reviewed)
    assert analyzed.status_code == 200, analyzed.text
    pending = analyzed.json()
    blocked = workflow_client.get(f"/api/workflows/{workflow_id}/bundle")
    assert blocked.status_code == 403

    approval = pending["pending_approval"]
    bundle_id = pending["bundle_review"]["bundle_id"]
    decision = {
        "workflow_id": workflow_id,
        "approval_id": approval["approval_id"],
        "bundle_id": bundle_id,
        "status": "approved",
        "reviewer_acknowledgement": True,
    }
    released = workflow_client.post(
        f"/api/workflows/{workflow_id}/approvals/bundle",
        json=decision,
    )
    assert released.status_code == 200, released.text
    assert released.json()["bundle_available"] is True

    download = workflow_client.get(f"/api/workflows/{workflow_id}/bundle")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert "diagnostic-bundle" in download.headers["content-disposition"]
