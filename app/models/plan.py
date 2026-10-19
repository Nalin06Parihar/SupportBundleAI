"""Models describing proposed evidence plans and local policy decisions."""

import json
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.models._common import (
    StrictModel,
    UTCDateTime,
    reject_sensitive_parameter_names,
    utc_now,
    validate_json_mapping,
    validate_warnings,
)


class CollectorName(StrEnum):
    """Names allowed in evidence-plan contracts."""

    SYSTEM_INFO = "system_info"
    RESOURCE_USAGE = "resource_usage"
    DISK_USAGE = "disk_usage"
    PROCESS_LIST = "process_list"
    SERVICE_STATUS = "service_status"
    APPLICATION_LOGS = "application_logs"
    NETWORK_INFO = "network_info"
    APPLICATION_CONFIG = "application_config"


class EvidenceStepStatus(StrEnum):
    PROPOSED = "proposed"
    POLICY_APPROVED = "policy_approved"
    POLICY_BLOCKED = "policy_blocked"
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
    HUMAN_APPROVED = "human_approved"
    PARTIALLY_APPROVED = "partially_approved"
    REJECTED = "rejected"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class EvidenceStep(StrictModel):
    """A single request to use an allow-listed collector."""

    step_id: UUID = Field(default_factory=uuid4)
    collector: CollectorName
    reason: str = Field(min_length=10, max_length=500)
    parameters: dict[str, object] = Field(default_factory=dict)
    required: bool = True
    status: EvidenceStepStatus = EvidenceStepStatus.PROPOSED

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, parameters: dict[str, object]) -> dict[str, object]:
        validate_json_mapping(parameters)
        reject_sensitive_parameter_names(parameters)
        return parameters


class PlanStatus(StrEnum):
    PROPOSED = "proposed"
    POLICY_VALIDATED = "policy_validated"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PARTIALLY_APPROVED = "partially_approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class EvidencePlan(StrictModel):
    """Validated AI-proposed collection plan awaiting local policy and approval."""

    plan_id: UUID = Field(default_factory=uuid4)
    issue_summary: str = Field(min_length=1, max_length=1000)
    suspected_category: str = Field(min_length=1, max_length=100)
    steps: list[EvidenceStep] = Field(min_length=1, max_length=10)
    status: PlanStatus = PlanStatus.PROPOSED
    created_at: UTCDateTime = Field(default_factory=utc_now)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("warnings")
    @classmethod
    def validate_plan_warnings(cls, warnings: list[str]) -> list[str]:
        return validate_warnings(warnings)

    @model_validator(mode="after")
    def validate_unique_steps(self) -> "EvidencePlan":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Evidence step IDs must be unique.")

        combinations = [
            (step.collector, json.dumps(step.parameters, sort_keys=True))
            for step in self.steps
        ]
        if len(combinations) != len(set(combinations)):
            raise ValueError("Duplicate collector and parameter combinations are not allowed.")
        return self


class PolicyDecisionStatus(StrEnum):
    APPROVED = "approved"
    APPROVED_WITH_CHANGES = "approved_with_changes"
    BLOCKED = "blocked"


class PolicyStepDecision(StrictModel):
    """Safe policy outcome for one proposed evidence step."""

    step_id: UUID
    status: PolicyDecisionStatus
    safe_reason: str = Field(min_length=1, max_length=500)
    modified_parameters: dict[str, object] | None = None

    @field_validator("modified_parameters")
    @classmethod
    def validate_modified_parameters(
        cls,
        parameters: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if parameters is not None:
            validate_json_mapping(parameters)
            reject_sensitive_parameter_names(parameters)
        return parameters


class PlanPolicyResult(StrictModel):
    """Policy decisions for all steps in an evidence plan."""

    plan_id: UUID
    status: PolicyDecisionStatus
    step_decisions: list[PolicyStepDecision] = Field(max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("warnings")
    @classmethod
    def validate_policy_warnings(cls, warnings: list[str]) -> list[str]:
        return validate_warnings(warnings)

    @model_validator(mode="after")
    def validate_unique_decisions(self) -> "PlanPolicyResult":
        step_ids = [decision.step_id for decision in self.step_decisions]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Policy decisions must have unique step IDs.")
        return self
