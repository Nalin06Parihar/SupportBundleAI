"""Collector for a bounded list of synthetic scenario processes."""

from typing import Any
from uuid import UUID

from app.collectors.base import BaseCollector, EvidenceUnavailable
from app.collectors._synthetic import read_json_config
from app.models.evidence import ArtifactMetadata
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition

MAX_PROCESSES = 50


class ProcessListCollector(BaseCollector):
    """Return up to fifty synthetic process records from scenario configuration."""

    collector_name = CollectorName.PROCESS_LIST
    description = "Read a bounded synthetic process list from the scenario."

    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        configuration, _, _ = read_json_config(self._scenario_loader, scenario)
        metrics: Any = configuration.get("resource_snapshot")
        processes = metrics.get("top_processes") if isinstance(metrics, dict) else None
        if not isinstance(processes, list):
            raise EvidenceUnavailable("Synthetic process information is unavailable.")
        bounded = [process for process in processes[:MAX_PROCESSES] if isinstance(process, dict)]
        if not bounded:
            raise EvidenceUnavailable("Synthetic process information is unavailable.")
        warnings = (
            ["Synthetic process output was limited to the approved count."]
            if len(processes) > MAX_PROCESSES
            else []
        )
        return {"processes": bounded}, [], warnings
