"""Collector for synthetic service state supplied by the scenario."""

from typing import Any
from uuid import UUID

from app.collectors.base import BaseCollector, EvidenceUnavailable
from app.collectors._synthetic import read_json_config
from app.models.evidence import ArtifactMetadata
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition


class ServiceStatusCollector(BaseCollector):
    """Return scenario-defined service names and states without querying the host."""

    collector_name = CollectorName.SERVICE_STATUS
    description = "Read synthetic service state from the scenario."
    scenario_evidence_extensions = (".json",)

    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        configuration, _, _ = read_json_config(self._scenario_loader, scenario)
        state: Any = configuration.get("scenario_state")
        if not isinstance(state, dict):
            raise EvidenceUnavailable("Synthetic service status is unavailable.")
        services = state.get("services")
        if isinstance(services, list):
            return {"services": [service for service in services if isinstance(service, dict)]}, [], []
        name = state.get("service_name") or state.get("application_service")
        status = state.get("service_state")
        if not isinstance(name, str) or not isinstance(status, str):
            raise EvidenceUnavailable("Synthetic service status is unavailable.")
        result: dict[str, object] = {"services": [{"name": name, "state": status}]}
        if isinstance(state.get("restart_count"), int):
            result["restart_count"] = state["restart_count"]
        if isinstance(state.get("exit_code"), int):
            result["exit_code"] = state["exit_code"]
        return result, [], []
