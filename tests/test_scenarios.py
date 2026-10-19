"""Focused tests for controlled synthetic scenario loading."""

import json

import pytest

from app.scenarios.loader import (
    ScenarioError,
    ScenarioLoader,
    ScenarioNotFoundError,
)


@pytest.mark.parametrize(
    "scenario_id",
    [
        "application-startup-failure",
        "high-cpu-usage",
        "network-dependency-timeout",
    ],
)
def test_bundled_scenario_loads_and_resolves_evidence(scenario_id: str) -> None:
    loader = ScenarioLoader()

    scenario = loader.load_scenario(scenario_id)

    assert scenario.scenario_id == scenario_id
    assert scenario.available_collectors
    assert scenario.expected_signals
    assert all(loader.resolve_evidence_file(path).is_file() for path in scenario.evidence_files)
    assert {item.scenario_id for item in loader.list_scenarios()} == {
        "application-startup-failure",
        "high-cpu-usage",
        "network-dependency-timeout",
    }


def test_unknown_scenario_is_rejected() -> None:
    with pytest.raises(ScenarioNotFoundError):
        ScenarioLoader().load_scenario("not-a-bundled-scenario")


@pytest.mark.parametrize(
    "reference",
    [
        "C:\\outside\\evidence.log",
        "\\\\demo-share\\evidence.log",
        "../outside/evidence.log",
        "logs/../../outside/evidence.log",
        "logs/invalid\x00name.log",
    ],
)
def test_evidence_reference_cannot_escape_controlled_root(
    tmp_path,
    reference: str,
) -> None:
    root = tmp_path / "controlled_environment"
    root.mkdir()
    loader = ScenarioLoader(root)

    with pytest.raises(ScenarioError):
        loader.resolve_evidence_file(reference)


@pytest.mark.parametrize("reference_kind", ["absolute", "traversal"])
def test_scenario_cannot_reference_outside_evidence(tmp_path, reference_kind: str) -> None:
    root = tmp_path / "controlled_environment"
    scenarios = root / "scenarios"
    scenarios.mkdir(parents=True)
    external_file = tmp_path / "outside.log"
    external_file.write_text("synthetic", encoding="utf-8")
    definition = {
        "scenario_id": "external-reference",
        "title": "External reference",
        "description": "Reject evidence outside the controlled directory.",
        "issue_category": "test",
        "available_collectors": ["application_logs"],
        "evidence_files": [
            str(external_file) if reference_kind == "absolute" else "../outside.log"
        ],
        "expected_signals": ["The file is outside the controlled root."],
    }
    (scenarios / "external-reference.yaml").write_text(
        json.dumps(definition),
        encoding="utf-8",
    )

    with pytest.raises(ScenarioError):
        ScenarioLoader(root).load_scenario(definition["scenario_id"])
