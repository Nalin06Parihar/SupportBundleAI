"""Collector for bounded synthetic log lines declared by a scenario."""

from uuid import UUID

from app.collectors.base import BaseCollector
from app.collectors._synthetic import file_artifact, read_log_lines
from app.models.evidence import ArtifactMetadata, ArtifactType
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition


class ApplicationLogCollector(BaseCollector):
    """Read only scenario-declared log files with bounded lines and file size."""

    collector_name = CollectorName.APPLICATION_LOGS
    description = "Read a bounded number of lines from the scenario's approved log."
    supported_parameter_names = ("max_lines",)
    parameter_limits = {"max_lines": (1, 100)}
    scenario_evidence_extensions = (".log",)

    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        lines, relative_path, size_bytes, warnings = read_log_lines(
            self._scenario_loader,
            scenario,
            step.parameters.get("max_lines"),
        )
        if not lines:
            warnings.append("The approved synthetic log contained no lines.")
        artifact = file_artifact(
            workflow_id=workflow_id,
            relative_path=relative_path,
            size_bytes=size_bytes,
            artifact_type=ArtifactType.APPLICATION_LOG,
            media_type="text/plain",
        )
        return {"lines": lines}, [artifact], warnings
