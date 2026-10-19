"""Collector for synthetic disk capacity and utilization metadata."""

from uuid import UUID

from app.collectors.base import BaseCollector, EvidenceUnavailable
from app.collectors._synthetic import read_json_config
from app.models.evidence import ArtifactMetadata
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition


class DiskUsageCollector(BaseCollector):
    """Return synthetic disk capacity and utilization supplied by the scenario."""

    collector_name = CollectorName.DISK_USAGE
    description = "Read synthetic disk capacity and utilization from the scenario."

    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        configuration, _, _ = read_json_config(self._scenario_loader, scenario)
        disk = configuration.get("disk_snapshot")
        if not isinstance(disk, dict):
            raise EvidenceUnavailable("Synthetic disk usage data is unavailable.")
        return {"disk": disk}, [], []
