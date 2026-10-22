"""Deterministic policy validation for controlled evidence plans."""

import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import ValidationError

from app.collectors.registry import CollectorRegistry, create_default_registry
from app.core.config import get_settings
from app.core.exceptions import (
    CollectorError,
    ConfigurationError,
    PolicyViolationError,
)
from app.models._common import (
    reject_sensitive_parameter_names,
    validate_json_mapping,
    validate_relative_path,
    validate_secret_free_text,
)
from app.models.plan import (
    EvidencePlan,
    EvidenceStep,
    PlanPolicyResult,
    PolicyDecisionStatus,
    PolicyStepDecision,
)
from app.models.scenario import ScenarioDefinition
from app.scenarios.loader import ScenarioError, ScenarioLoader

_SENSITIVE_PARAMETER_FRAGMENTS = (
    "apikey",
    "password",
    "passwd",
    "token",
    "authorization",
    "privatekey",
    "secret",
    "credential",
    "command",
    "shell",
    "powershell",
    "script",
    "executable",
    "environment",
    "directory",
    "dir",
    "filepath",
    "file",
    "path",
    "target",
    "service",
    "process",
    "dump",
    "arbitrary",
)
_SENSITIVE_PARAMETER_VALUES = {
    "*",
    "all",
    "allfiles",
    "alldirectories",
    "allprocesses",
    "allservices",
    "authentication",
    "commandline",
    "credentials",
    "environment",
    "environmentdump",
    "environmentvariables",
    "env",
    "envdump",
}
_ALLOWED_EVIDENCE_EXTENSIONS = {".json", ".log"}


class PlanPolicyValidator:
    """Validate plan steps against the local collector allow-list and scenario."""

    def __init__(
        self,
        registry: CollectorRegistry | None = None,
        scenario_loader: ScenarioLoader | None = None,
    ) -> None:
        self._registry = registry or create_default_registry()
        self._scenario_loader = scenario_loader or ScenarioLoader()

    def validate(
        self,
        plan: EvidencePlan,
        scenario: ScenarioDefinition,
    ) -> PlanPolicyResult:
        """Return deterministic per-step decisions without changing the plan."""
        try:
            settings = get_settings()
        except ValidationError:
            raise ConfigurationError("Policy configuration is invalid.") from None

        maximum_steps = settings.max_tool_calls
        maximum_file_size = max(
            1,
            int(settings.max_log_file_size_mb * 1024 * 1024),
        )
        scenario_file_error = self._validate_scenario_files(
            scenario,
            maximum_file_size,
        )

        decisions: list[PolicyStepDecision] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for position, step in enumerate(plan.steps):
            if position >= maximum_steps:
                decisions.append(
                    self._blocked(step, "Plan exceeds the configured collector-call limit.")
                )
                continue

            decision, duplicate_key = self._validate_step(
                step,
                scenario,
                scenario_file_error,
            )
            if decision.status != PolicyDecisionStatus.BLOCKED:
                if duplicate_key in seen:
                    decision = self._blocked(
                        step,
                        "An equivalent collection step is already present in the plan.",
                    )
                else:
                    seen.add(duplicate_key)
            decisions.append(decision)

        if any(item.status == PolicyDecisionStatus.BLOCKED for item in decisions):
            status = PolicyDecisionStatus.BLOCKED
        elif any(
            item.status == PolicyDecisionStatus.APPROVED_WITH_CHANGES
            for item in decisions
        ):
            status = PolicyDecisionStatus.APPROVED_WITH_CHANGES
            warnings.append("One or more collection steps were restricted by local policy.")
        else:
            status = PolicyDecisionStatus.APPROVED

        return PlanPolicyResult(
            plan_id=plan.plan_id,
            status=status,
            step_decisions=decisions,
            warnings=warnings,
        )

    def _validate_step(
        self,
        step: EvidenceStep,
        scenario: ScenarioDefinition,
        scenario_file_error: str | None,
    ) -> tuple[PolicyStepDecision, str]:
        try:
            collector = self._registry.get(step.collector)
        except (CollectorError, PolicyViolationError, ValueError, TypeError):
            return (
                self._blocked(
                    step,
                    "Collector is not registered in the local allow-list.",
                ),
                "",
            )

        if collector.collector_name not in scenario.available_collectors:
            return self._blocked(
                step,
                "This scenario does not declare evidence for the requested collector.",
            ), ""
        if scenario_file_error is not None:
            return self._blocked(step, scenario_file_error), ""
        if collector.requires_scenario_evidence:
            available_extensions = {
                PurePosixPath(reference.replace("\\", "/")).suffix.lower()
                for reference in scenario.evidence_files
            }
            if not available_extensions.intersection(
                collector.scenario_evidence_extensions
            ):
                return self._blocked(
                    step,
                    "This scenario does not provide the required evidence-file type.",
                ), ""

        parameters = step.parameters
        if not isinstance(parameters, dict):
            return self._blocked(step, "Collection parameters are invalid."), ""
        try:
            validate_json_mapping(parameters)
            reject_sensitive_parameter_names(parameters)
            self._validate_parameter_values(parameters)
        except ValueError:
            return self._blocked(
                step,
                "Collection parameters contain unsafe or invalid values.",
            ), ""

        normalized = dict(parameters)
        removed = set(normalized) - set(collector.supported_parameter_names)
        if any(self._is_sensitive_parameter_name(name) for name in removed):
            return self._blocked(
                step,
                "Collection parameters request prohibited access or sensitive data.",
            ), ""
        for name in removed:
            normalized.pop(name)

        changed = bool(removed)
        for name, (minimum, maximum) in collector.parameter_limits.items():
            if name not in normalized:
                continue
            value = normalized[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                return self._blocked(
                    step,
                    "A collector-specific limit is invalid.",
                ), ""
            if value > maximum:
                normalized[name] = maximum
                changed = True

        missing_required = set(collector.required_parameter_names) - set(normalized)
        if missing_required:
            return self._blocked(
                step,
                "A required collector parameter is missing.",
            ), ""

        canonical_parameters = dict(normalized)
        for name, (_, maximum) in collector.parameter_limits.items():
            canonical_parameters.setdefault(name, maximum)
        duplicate_key = json.dumps(
            [collector.collector_name.value, canonical_parameters],
            sort_keys=True,
            separators=(",", ":"),
        )
        if changed:
            reason = (
                "Unsupported optional parameters were removed and/or limits were reduced."
            )
            return (
                PolicyStepDecision(
                    step_id=step.step_id,
                    status=PolicyDecisionStatus.APPROVED_WITH_CHANGES,
                    safe_reason=reason,
                    modified_parameters=normalized,
                ),
                duplicate_key,
            )
        return (
            PolicyStepDecision(
                step_id=step.step_id,
                status=PolicyDecisionStatus.APPROVED,
                safe_reason="Collector and parameters are permitted for this scenario.",
            ),
            duplicate_key,
        )

    def _validate_scenario_files(
        self,
        scenario: ScenarioDefinition,
        maximum_file_size: int,
    ) -> str | None:
        for reference in scenario.evidence_files:
            try:
                normalized = validate_relative_path(reference)
                suffix = PurePosixPath(normalized).suffix.lower()
                if suffix not in _ALLOWED_EVIDENCE_EXTENSIONS:
                    return "Scenario references a disallowed evidence-file type."
                path = self._scenario_loader.resolve_evidence_file(normalized)
                if path.stat().st_size > maximum_file_size:
                    return "Scenario evidence exceeds the configured file-size limit."
            except (ValueError, ScenarioError, OSError):
                return "Scenario evidence references are not safely controlled."
        return None

    @staticmethod
    def _validate_parameter_values(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                PlanPolicyValidator._validate_parameter_values(item)
        elif isinstance(value, list):
            for item in value:
                PlanPolicyValidator._validate_parameter_values(item)
        elif isinstance(value, str):
            validate_secret_free_text(value)
            normalized_value = re.sub(r"[^a-z0-9*]", "", value.lower())
            if (
                "\x00" in value
                or PurePosixPath(value).is_absolute()
                or PureWindowsPath(value).is_absolute()
                or PureWindowsPath(value).drive
                or any(part == ".." for part in value.replace("\\", "/").split("/"))
                or normalized_value in _SENSITIVE_PARAMETER_VALUES
            ):
                raise ValueError("Unsafe parameter value.")

    @staticmethod
    def _is_sensitive_parameter_name(name: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", name.lower())
        return any(fragment in normalized for fragment in _SENSITIVE_PARAMETER_FRAGMENTS)

    @staticmethod
    def _blocked(step: EvidenceStep, reason: str) -> PolicyStepDecision:
        return PolicyStepDecision(
            step_id=step.step_id,
            status=PolicyDecisionStatus.BLOCKED,
            safe_reason=reason,
        )
