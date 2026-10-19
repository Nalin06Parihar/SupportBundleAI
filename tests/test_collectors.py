"""Focused tests for the deterministic synthetic collector layer."""

from uuid import uuid4

import pytest

from app.collectors.log_collector import ApplicationLogCollector
from app.collectors.registry import CollectorRegistry, create_default_registry
from app.core.exceptions import CollectorError, PolicyViolationError
from app.models.evidence import CollectionStatus, SensitivityStatus
from app.models.plan import CollectorName, EvidenceStep
from app.scenarios.loader import ScenarioLoader


def make_step(collector: CollectorName, **parameters: object) -> EvidenceStep:
    return EvidenceStep(
        collector=collector,
        reason="Collect bounded synthetic diagnostic evidence.",
        parameters=parameters,
    )


def test_default_registry_executes_every_allowed_scenario_collector() -> None:
    registry = create_default_registry()
    loader = ScenarioLoader()
    workflow_id = uuid4()

    assert len(registry.metadata()) == 8
    for scenario in loader.list_scenarios():
        for collector_name in scenario.available_collectors:
            result = registry.execute(
                make_step(collector_name),
                scenario,
                workflow_id=workflow_id,
            )
            assert result.collector == collector_name
            assert result.workflow_id == workflow_id
            assert result.status in {CollectionStatus.SUCCESS, CollectionStatus.PARTIAL}
            assert result.duration_ms >= 0
            assert result.data
            for artifact in result.artifacts:
                assert artifact.sanitized is False
                assert artifact.sensitivity_status == SensitivityStatus.NOT_SCANNED
                assert not artifact.relative_path.startswith(("/", "\\"))


def test_registry_rejects_unknown_or_unregistered_collectors() -> None:
    registry = create_default_registry()
    with pytest.raises(CollectorError):
        registry.get("not-a-collector")
    with pytest.raises(CollectorError):
        CollectorRegistry().get(CollectorName.SYSTEM_INFO)


@pytest.mark.parametrize(
    ("collector_name", "extension"),
    [
        (CollectorName.APPLICATION_CONFIG, ".json"),
        (CollectorName.APPLICATION_LOGS, ".log"),
    ],
)
@pytest.mark.parametrize(
    "reference_template",
    [
        "C:\\outside\\external{extension}",
        "\\\\demo-share\\external{extension}",
        "../external{extension}",
        "configs/../../external{extension}",
        "configs/invalid\x00{extension}",
    ],
)
def test_file_collectors_reject_escaping_scenario_references(
    collector_name: CollectorName,
    extension: str,
    reference_template: str,
) -> None:
    loader = ScenarioLoader()
    scenario = loader.load_scenario("application-startup-failure").model_copy(
        update={"evidence_files": [reference_template.format(extension=extension)]}
    )
    collector = create_default_registry().get(collector_name)

    with pytest.raises(PolicyViolationError):
        collector.collect(
            make_step(collector_name),
            scenario,
            workflow_id=uuid4(),
        )


def test_missing_log_source_returns_safe_skipped_result(tmp_path) -> None:
    from shutil import copytree
    from pathlib import Path

    root = tmp_path / "controlled_environment"
    copytree(Path("controlled_environment"), root)
    loader = ScenarioLoader(root)
    scenario = loader.load_scenario("application-startup-failure")
    (root / "logs" / "application-startup-failure.log").unlink()

    result = ApplicationLogCollector(loader).collect(
        make_step(CollectorName.APPLICATION_LOGS),
        scenario,
        workflow_id=uuid4(),
    )

    assert result.status == CollectionStatus.SKIPPED
    assert result.error is None
    assert result.artifacts == []
    assert "unavailable" in result.warnings[0]
