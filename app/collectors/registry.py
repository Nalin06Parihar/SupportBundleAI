"""Allow-listed registry for fixed deterministic collector implementations."""

from uuid import UUID

from app.core.exceptions import CollectorError
from app.models.evidence import CollectorResult
from app.models.plan import CollectorName, EvidenceStep
from app.models.scenario import ScenarioDefinition
from app.collectors.base import BaseCollector
from app.collectors.disk_info import DiskUsageCollector
from app.collectors.log_collector import ApplicationLogCollector
from app.collectors.network_info import NetworkInfoCollector
from app.collectors.process_info import ProcessListCollector
from app.collectors.service_info import ServiceStatusCollector
from app.collectors.system_info import ResourceUsageCollector, SystemInfoCollector
from app.collectors.application_config import ApplicationConfigCollector


class CollectorRegistry:
    """Map contract-defined collector names to explicitly registered instances."""

    def __init__(self, collectors: tuple[BaseCollector, ...] | list[BaseCollector] = ()):
        self._collectors: dict[CollectorName, BaseCollector] = {}
        for collector in collectors:
            self.register(collector)

    def register(self, collector: BaseCollector) -> None:
        """Register a collector instance under its fixed enum name."""
        approved_types = (
            SystemInfoCollector,
            ResourceUsageCollector,
            DiskUsageCollector,
            ProcessListCollector,
            ServiceStatusCollector,
            ApplicationLogCollector,
            NetworkInfoCollector,
            ApplicationConfigCollector,
        )
        if type(collector) not in approved_types:
            raise CollectorError("Only approved collector implementations may be registered.")
        if collector.collector_name in self._collectors:
            raise CollectorError("Collector is already registered.")
        self._collectors[collector.collector_name] = collector

    def get(self, collector_name: CollectorName | str) -> BaseCollector:
        """Get a registered collector by the existing contract enum."""
        try:
            name = CollectorName(collector_name)
        except (TypeError, ValueError):
            raise CollectorError("Requested collector is not approved.") from None
        collector = self._collectors.get(name)
        if collector is None:
            raise CollectorError("Requested collector is not registered.")
        return collector

    def execute(
        self,
        step: EvidenceStep,
        scenario: ScenarioDefinition,
        *,
        workflow_id: UUID,
    ) -> CollectorResult:
        """Execute the fixed registered implementation selected by EvidenceStep."""
        return self.get(step.collector).collect(
            step,
            scenario,
            workflow_id=workflow_id,
        )

    def metadata(self) -> list[dict[str, object]]:
        """Return safe collector descriptions for future planning interfaces."""
        return [
            {
                "name": collector.collector_name.value,
                "description": collector.description,
                "supported_parameters": list(collector.supported_parameter_names),
            }
            for collector in sorted(
                self._collectors.values(),
                key=lambda item: item.collector_name.value,
            )
        ]


def create_default_registry() -> CollectorRegistry:
    """Create the allow-list of deterministic synthetic Phase 6 collectors."""
    return CollectorRegistry(
        [
            SystemInfoCollector(),
            ResourceUsageCollector(),
            DiskUsageCollector(),
            ProcessListCollector(),
            ServiceStatusCollector(),
            ApplicationLogCollector(),
            NetworkInfoCollector(),
            ApplicationConfigCollector(),
        ]
    )
