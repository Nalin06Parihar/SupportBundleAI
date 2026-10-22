"""Collectors for synthetic system and resource metadata."""

from typing import Any

from uuid import UUID

from app.collectors.base import BaseCollector, EvidenceUnavailable
from app.collectors._synthetic import read_json_config, synthetic_host
from app.models.evidence import ArtifactMetadata
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition


class SystemInfoCollector(BaseCollector):
    """Return OS, hostname, architecture, and boot metadata from the scenario."""

    collector_name = CollectorName.SYSTEM_INFO
    description = "Read synthetic operating-system and host metadata from the scenario."
    scenario_evidence_extensions = (".json",)

    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        configuration, _, _ = read_json_config(self._scenario_loader, scenario)
        system = configuration.get("synthetic_system")
        host = synthetic_host(configuration)
        if not isinstance(system, dict) and not host:
            raise EvidenceUnavailable("Synthetic system metadata is unavailable.")
        result: dict[str, object] = {}
        if isinstance(system, dict):
            result["system"] = {
                key: system[key]
                for key in ("os_name", "os_version", "architecture", "boot_time", "uptime_seconds")
                if key in system
            }
        if host:
            result["host"] = {
                key: host[key] for key in ("hostname", "address") if key in host
            }
        warning = [] if result.get("system") and result.get("host") else [
            "Some synthetic system metadata was not provided."
        ]
        return result, [], warning


class ResourceUsageCollector(BaseCollector):
    """Return synthetic CPU and memory measurements from the scenario."""

    collector_name = CollectorName.RESOURCE_USAGE
    description = "Read synthetic CPU and memory metrics from the scenario."
    scenario_evidence_extensions = (".json",)

    def _collect_synthetic(
        self,
        scenario: ScenarioDefinition,
        step: EvidenceStep,
        workflow_id: UUID,
    ) -> tuple[dict[str, object], list[ArtifactMetadata], list[str]]:
        configuration, _, _ = read_json_config(self._scenario_loader, scenario)
        metrics: Any = configuration.get("resource_snapshot")
        if not isinstance(metrics, dict):
            raise EvidenceUnavailable("Synthetic resource metrics are unavailable.")
        result = {
            key: metrics[key]
            for key in (
                "cpu_percent",
                "cpu_alert_threshold_percent",
                "memory_percent",
                "memory_alert_threshold_percent",
            )
            if key in metrics
        }
        if not result:
            raise EvidenceUnavailable("Synthetic resource metrics are unavailable.")
        expected = {"cpu_percent", "memory_percent"}
        warnings = [] if expected.issubset(result) else [
            "Some synthetic resource metrics were not provided."
        ]
        return {"resources": result}, [], warnings
