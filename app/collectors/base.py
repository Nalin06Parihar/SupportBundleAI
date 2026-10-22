"""Shared safe execution interface for deterministic synthetic collectors."""

from abc import ABC, abstractmethod
from time import perf_counter
from typing import ClassVar
from uuid import UUID

from app.core.exceptions import CollectorError, PolicyViolationError
from app.models._common import utc_now
from app.models.evidence import (
    ArtifactMetadata,
    CollectionStatus,
    CollectorResult,
)
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition
from app.scenarios.loader import ScenarioLoader


class EvidenceUnavailable(Exception):
    """Expected absence or unusability of a scenario evidence source."""


class BaseCollector(ABC):
    """Base class that validates scope and standardizes collector outcomes."""

    collector_name: CollectorName
    description: str
    supported_parameter_names: tuple[str, ...] = ()
    required_parameter_names: tuple[str, ...] = ()
    parameter_limits: ClassVar[dict[str, tuple[int, int]]] = {}
    scenario_evidence_extensions: tuple[str, ...] = ()
    requires_scenario_evidence: bool = True

    def __init__(self, scenario_loader: ScenarioLoader | None = None) -> None:
        self._scenario_loader = scenario_loader or ScenarioLoader()

    def collect(
        self,
        step: EvidenceStep,
        scenario: ScenarioDefinition,
        *,
        workflow_id: UUID,
    ) -> CollectorResult:
        """Collect one validated step from controlled synthetic scenario data."""
        started = perf_counter()
        if step.collector != self.collector_name:
            raise PolicyViolationError("Evidence step does not match this collector.")
        unsupported = set(step.parameters) - set(self.supported_parameter_names)
        if unsupported:
            raise PolicyViolationError("Evidence step contains unsupported parameters.")
        if self.collector_name not in scenario.available_collectors:
            return self._result(
                step,
                workflow_id,
                CollectionStatus.SKIPPED,
                started,
                warnings=["This scenario does not provide this collector's evidence."],
            )

        try:
            data, artifacts, warnings = self._collect_synthetic(
                scenario,
                step,
                workflow_id,
            )
            status = CollectionStatus.PARTIAL if warnings else CollectionStatus.SUCCESS
            return self._result(
                step,
                workflow_id,
                status,
                started,
                data=data,
                artifacts=artifacts,
                warnings=warnings,
            )
        except EvidenceUnavailable as error:
            return self._result(
                step,
                workflow_id,
                CollectionStatus.SKIPPED,
                started,
                warnings=[str(error)],
            )

    @abstractmethod
    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        """Read only the synthetic evidence declared by this scenario."""

    def _result(
        self,
        step: EvidenceStep,
        workflow_id: UUID,
        status: CollectionStatus,
        started: float,
        *,
        data: dict[str, object] | None = None,
        artifacts: list[ArtifactMetadata] | None = None,
        warnings: list[str] | None = None,
    ) -> CollectorResult:
        try:
            return CollectorResult(
                workflow_id=workflow_id,
                step_id=step.step_id,
                collector=self.collector_name,
                status=status,
                collected_at=utc_now(),
                data=data or {},
                artifacts=artifacts or [],
                warnings=warnings or [],
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
            )
        except (TypeError, ValueError):
            raise CollectorError(
                f"The {self.collector_name.value} collector produced invalid evidence."
            ) from None
