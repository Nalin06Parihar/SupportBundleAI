"""Validation tests for workflow identity and future graph state."""

from datetime import UTC, datetime
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.issue import IssueRequest
from app.models.workflow import (
    WorkflowEvent,
    WorkflowEventType,
    WorkflowState,
    WorkflowStatus,
    WorkflowSummary,
)


def make_issue() -> IssueRequest:
    return IssueRequest(
        title="Startup issue",
        description="Application startup fails consistently.",
        application_name="Example App",
    )


def test_valid_minimal_workflow_state_is_accepted() -> None:
    state = WorkflowState(issue=make_issue())
    assert state.status == WorkflowStatus.RECEIVED
    assert state.collector_results == []


def test_workflow_uuids_and_datetimes_serialize_to_json() -> None:
    state = WorkflowState(issue=make_issue())
    encoded = json.loads(state.model_dump_json())
    assert encoded["workflow_id"] == str(state.workflow_id)
    assert encoded["created_at"].endswith("Z")


def test_workflow_default_lists_do_not_share_mutable_state() -> None:
    first = WorkflowState(issue=make_issue())
    second = WorkflowState(issue=make_issue())
    first.warnings.append("first only")
    assert second.warnings == []


def test_workflow_state_json_serialization_has_no_runtime_or_secret_fields() -> None:
    state_data = WorkflowState(issue=make_issue()).model_dump(mode="json")
    assert "GROQ_API_KEY" not in state_data
    assert "api_key" not in state_data
    assert "collector_instance" not in state_data
    assert "database_session" not in state_data


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowEvent(
            event_type=WorkflowEventType.WORKFLOW_CREATED,
            message="Workflow created.",
            created_at=datetime(2026, 1, 1),
        )


def test_offset_datetime_is_normalized_to_utc() -> None:
    state = WorkflowState(
        issue=make_issue(),
        created_at=datetime.fromisoformat("2026-01-01T03:00:00+03:00"),
    )
    assert state.created_at.tzinfo == UTC
    assert state.created_at.hour == 0


def test_workflow_summary_has_non_negative_warning_count() -> None:
    with pytest.raises(ValidationError):
        WorkflowSummary(
            workflow_id=uuid4(),
            issue_id=uuid4(),
            status=WorkflowStatus.RECEIVED,
            current_stage="received",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            warning_count=-1,
        )
