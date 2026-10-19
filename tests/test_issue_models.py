"""Validation tests for user-submitted issue contracts."""

import pytest
from pydantic import ValidationError

from app.models.issue import IssueRequest


def test_valid_issue_normalizes_and_deduplicates_symptoms() -> None:
    issue = IssueRequest(
        title="  Login issue  ",
        description="  Users cannot sign in to the application.  ",
        application_name="  Example App ",
        symptoms=[" timeout ", "", "timeout", "Error shown"],
        scenario_id="  scenario_01 ",
    )

    assert issue.title == "Login issue"
    assert issue.symptoms == ["timeout", "Error shown"]
    assert issue.scenario_id == "scenario_01"


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "  ", "description": "A sufficiently long description", "application_name": "App"},
        {"title": "Valid title", "description": "short", "application_name": "App"},
        {
            "title": "Valid title",
            "description": "x" * 5001,
            "application_name": "App",
        },
    ],
)
def test_invalid_issue_lengths_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        IssueRequest(**payload)


def test_empty_scenario_id_becomes_none() -> None:
    issue = IssueRequest(
        title="Valid title",
        description="A sufficiently long description",
        application_name="App",
        scenario_id="   ",
    )
    assert issue.scenario_id is None


def test_too_many_symptoms_are_rejected() -> None:
    with pytest.raises(ValidationError):
        IssueRequest(
            title="Valid title",
            description="A sufficiently long description",
            application_name="App",
            symptoms=["symptom"] * 21,
        )


def test_overlong_symptom_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IssueRequest(
            title="Valid title",
            description="A sufficiently long description",
            application_name="App",
            symptoms=["x" * 301],
        )


@pytest.mark.parametrize("scenario_id", ["../escape", "unsafe id", "a/b"])
def test_unsafe_scenario_identifier_is_rejected(scenario_id: str) -> None:
    with pytest.raises(ValidationError):
        IssueRequest(
            title="Valid title",
            description="A sufficiently long description",
            application_name="App",
            scenario_id=scenario_id,
        )
