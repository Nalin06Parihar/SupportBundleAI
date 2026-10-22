"""Collector for the approved synthetic JSON application configuration."""

from uuid import UUID

from app.collectors.base import BaseCollector
from app.collectors._synthetic import file_artifact, read_json_config
from app.models.evidence import ArtifactMetadata, ArtifactType
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition


class ApplicationConfigCollector(BaseCollector):
    """Return an approved scenario configuration and relative artifact metadata."""

    collector_name = CollectorName.APPLICATION_CONFIG
    description = "Read the scenario's approved synthetic JSON configuration."
    scenario_evidence_extensions = (".json",)

    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        configuration, relative_path, size_bytes = read_json_config(
            self._scenario_loader,
            scenario,
        )
        artifact = file_artifact(
            workflow_id=workflow_id,
            relative_path=relative_path,
            size_bytes=size_bytes,
            artifact_type=ArtifactType.APPLICATION_CONFIGURATION,
            media_type="application/json",
        )
        return {"configuration": configuration}, [artifact], []
