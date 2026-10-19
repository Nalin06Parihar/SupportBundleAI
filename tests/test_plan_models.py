"""Validation tests for evidence plans and policy decisions."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.plan import (
    CollectorName,
    EvidencePlan,
    EvidenceStep,
    PlanPolicyResult,
    PlanStatus,
    PolicyDecisionStatus,
    PolicyStepDecision,
)


def make_step(**overrides: object) -> EvidenceStep:
    values: dict[str, object] = {
        "collector": CollectorName.SYSTEM_INFO,
        "reason": "Collect basic system information",
    }
    values.update(overrides)
    return EvidenceStep(**values)


def make_plan(steps: list[EvidenceStep]) -> EvidencePlan:
    return EvidencePlan(
        issue_summary="Application startup is failing",
        suspected_category="application",
        steps=steps,
    )


def test_valid_plan_is_accepted() -> None:
    plan = make_plan([make_step()])
    assert plan.status == PlanStatus.PROPOSED


def test_empty_plan_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_plan([])


def test_more_than_ten_steps_are_rejected() -> None:
    steps = [
        make_step(
            collector=collector,
            parameters={"index": index},
        )
        for index, collector in enumerate(CollectorName)
    ]
    steps.extend(
        make_step(
            collector=CollectorName.SYSTEM_INFO,
            parameters={"extra": index},
        )
        for index in range(3)
    )
    with pytest.raises(ValidationError):
        make_plan(steps)


def test_unknown_collector_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_step(collector="arbitrary_collector")


def test_empty_reason_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_step(reason=" ")


def test_duplicate_step_ids_are_rejected() -> None:
    step_id = uuid4()
    with pytest.raises(ValidationError):
        make_plan([make_step(step_id=step_id), make_step(step_id=step_id, parameters={"x": 1})])


@pytest.mark.parametrize(
    "parameters",
    [
        {"command": "whoami"},
        {"nested": {"powershell_command": "Get-Process"}},
        {"api_key": "test-placeholder-key"},
        {"nested": {"private-key": "placeholder"}},
        {"client_secret": "test-placeholder"},
        {"access_token": "test-placeholder"},
        {"shellCommand": "unsafe"},
    ],
)
def test_forbidden_parameter_names_are_rejected(
    parameters: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        make_step(parameters=parameters)


def test_json_compatible_parameters_are_accepted() -> None:
    step = make_step(parameters={"limit": 5, "include": ["cpu", "memory"]})
    assert step.parameters["limit"] == 5


def test_arbitrary_python_parameter_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_step(parameters={"value": object()})


def test_duplicate_collector_and_parameter_combinations_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_plan(
            [
                make_step(parameters={"limit": 2}),
                make_step(parameters={"limit": 2}),
            ]
        )


def test_policy_decisions_are_typed_and_unique() -> None:
    step_id = uuid4()
    result = PlanPolicyResult(
        plan_id=uuid4(),
        status=PolicyDecisionStatus.APPROVED,
        step_decisions=[
            PolicyStepDecision(
                step_id=step_id,
                status=PolicyDecisionStatus.APPROVED,
                safe_reason="Collector is allow-listed.",
                modified_parameters={},
            )
        ],
    )
    assert result.step_decisions[0].step_id == step_id


def test_policy_rejects_command_parameters() -> None:
    with pytest.raises(ValidationError):
        PolicyStepDecision(
            step_id=uuid4(),
            status=PolicyDecisionStatus.APPROVED_WITH_CHANGES,
            safe_reason="The policy changed the request.",
            modified_parameters={"script": "unsafe"},
        )
