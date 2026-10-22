"""Focused tests for deterministic collection-plan policy validation."""

import pytest

from app.collectors.registry import create_default_registry
from app.models.plan import (
    CollectorName,
    EvidencePlan,
    EvidenceStep,
    PlanPolicyResult,
    PolicyDecisionStatus,
)
from app.models.scenario import ScenarioDefinition
from app.scenarios.loader import ScenarioLoader
from app.security.policy import PlanPolicyValidator


def make_step(
    collector: str,
    parameters: dict[str, object] | None = None,
) -> EvidenceStep:
    step = EvidenceStep(
        collector=CollectorName.SYSTEM_INFO,
        reason="Collect relevant synthetic diagnostic evidence.",
    )
    collector_value: CollectorName | str
    try:
        collector_value = CollectorName(collector)
    except ValueError:
        collector_value = collector
    return step.model_copy(
        update={"collector": collector_value, "parameters": parameters or {}}
    )


def make_plan(*steps: EvidenceStep) -> EvidencePlan:
    return EvidencePlan(
        issue_summary="Investigate synthetic application performance.",
        suspected_category="resource_usage",
        steps=list(steps),
    )


@pytest.fixture
def scenario() -> ScenarioDefinition:
    return ScenarioLoader().load_scenario("high-cpu-usage")


@pytest.fixture
def validator() -> PlanPolicyValidator:
    return PlanPolicyValidator(registry=create_default_registry())


def test_valid_controlled_plan_is_approved(validator, scenario) -> None:
    plan = make_plan(
        make_step("resource_usage"),
        make_step("process_list", {"max_processes": 10}),
    )

    result = validator.validate(plan, scenario)

    assert isinstance(result, PlanPolicyResult)
    assert result.status == PolicyDecisionStatus.APPROVED
    assert all(
        decision.status == PolicyDecisionStatus.APPROVED
        for decision in result.step_decisions
    )


@pytest.mark.parametrize(
    ("collector", "parameters"),
    [
        ("application_logs", {"shell_command": "whoami"}),
        ("application_config", {"api_key": "synthetic-secret"}),
        ("application_config", {"path": "../outside.json"}),
        ("unknown_collector", {}),
    ],
)
def test_dangerous_parameters_and_unknown_collectors_are_blocked(
    validator,
    scenario,
    collector: str,
    parameters: dict[str, object],
) -> None:
    plan = make_plan(make_step(collector, parameters))

    result = validator.validate(plan, scenario)

    assert result.status == PolicyDecisionStatus.BLOCKED
    assert result.step_decisions[0].status == PolicyDecisionStatus.BLOCKED


@pytest.mark.parametrize(
    ("collector", "parameter", "excessive"),
    [
        ("application_logs", "max_lines", 10_000),
        ("process_list", "max_processes", 10_000),
    ],
)
def test_excessive_reducible_limits_are_restricted(
    validator,
    scenario,
    collector: str,
    parameter: str,
    excessive: int,
) -> None:
    if collector == "application_logs":
        scenario = ScenarioLoader().load_scenario("network-dependency-timeout")
    step = make_step(collector, {parameter: excessive})
    plan = make_plan(step)

    result = validator.validate(plan, scenario)
    decision = result.step_decisions[0]

    assert result.status == PolicyDecisionStatus.APPROVED_WITH_CHANGES
    assert decision.status == PolicyDecisionStatus.APPROVED_WITH_CHANGES
    assert decision.modified_parameters is not None
    assert decision.modified_parameters[parameter] == (
        100 if parameter == "max_lines" else 50
    )
    assert plan.steps[0].parameters[parameter] == excessive


def test_scenario_unsupported_evidence_is_blocked(validator) -> None:
    startup_scenario = ScenarioLoader().load_scenario("application-startup-failure")
    plan = make_plan(make_step("resource_usage"))

    result = validator.validate(plan, startup_scenario)

    assert result.status == PolicyDecisionStatus.BLOCKED
    assert "scenario" in result.step_decisions[0].safe_reason.lower()


def test_unsafe_scenario_reference_is_blocked_without_mutating_plan(
    validator,
    scenario,
) -> None:
    step = make_step("application_logs", {"max_lines": 20})
    plan = make_plan(step)
    before = plan.model_dump(mode="json")
    unsafe_scenario = scenario.model_copy(
        update={"evidence_files": ["logs/../../outside.log"]}
    )

    result = validator.validate(plan, unsafe_scenario)

    assert result.status == PolicyDecisionStatus.BLOCKED
    assert plan.model_dump(mode="json") == before
    assert plan.steps[0].parameters == {"max_lines": 20}


def test_equivalent_collection_steps_are_blocked(validator, scenario) -> None:
    scenario = ScenarioLoader().load_scenario("network-dependency-timeout")
    first = make_step("application_logs")
    second = make_step("application_logs", {"max_lines": 100})
    plan = make_plan(first, second)

    result = validator.validate(plan, scenario)

    assert result.status == PolicyDecisionStatus.BLOCKED
    assert result.step_decisions[0].status == PolicyDecisionStatus.APPROVED
    assert result.step_decisions[1].status == PolicyDecisionStatus.BLOCKED


def test_plan_respects_configured_tool_call_limit(validator) -> None:
    scenario = ScenarioLoader().load_scenario("network-dependency-timeout").model_copy(
        update={
            "available_collectors": list(CollectorName),
        }
    )
    plan = make_plan(
        make_step("system_info"),
        make_step("resource_usage"),
        make_step("disk_usage"),
        make_step("process_list"),
        make_step("service_status"),
        make_step("network_info"),
        make_step("application_config"),
    )

    result = validator.validate(plan, scenario)

    assert result.status == PolicyDecisionStatus.BLOCKED
    assert result.step_decisions[5].status == PolicyDecisionStatus.APPROVED
    assert result.step_decisions[6].status == PolicyDecisionStatus.BLOCKED
