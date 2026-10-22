"""Collector for synthetic network and dependency-connectivity evidence."""

from uuid import UUID

from app.collectors.base import BaseCollector, EvidenceUnavailable
from app.collectors._synthetic import read_json_config, synthetic_host
from app.models.evidence import ArtifactMetadata
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition


class NetworkInfoCollector(BaseCollector):
    """Return synthetic interface or dependency state from the scenario."""

    collector_name = CollectorName.NETWORK_INFO
    description = "Read synthetic network interface or dependency status."
    scenario_evidence_extensions = (".json",)

    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        configuration, _, _ = read_json_config(self._scenario_loader, scenario)
        network = configuration.get("network_snapshot")
        if isinstance(network, dict):
            return {"network": network}, [], []
        dependency = configuration.get("dependency_config")
        state = configuration.get("scenario_state")
        host = synthetic_host(configuration)
        if isinstance(dependency, dict) and isinstance(state, dict):
            result: dict[str, object] = {
                "dependency": dependency,
                "dependency_state": state.get("dependency_state"),
            }
            if host:
                result["host"] = host
            return result, [], []
        raise EvidenceUnavailable("Synthetic network information is unavailable.")
