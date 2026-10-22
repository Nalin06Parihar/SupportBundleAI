"""Structured analysis of explicitly approved sanitized evidence."""

import asyncio
import json
import logging
import re
from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError

from app.agent.analysis_prompts import build_analysis_messages
from app.agent.groq_client import ChatModelProvider, GroqChatModelProvider
from app.core.config import Settings, get_settings
from app.core.exceptions import AIProviderError
from app.models._common import StrictModel, validate_secret_free_text
from app.models.analysis import (
    AnalysisResult,
    ConfidenceLevel,
    EvidenceFinding,
    EvidenceReference,
    EvidenceSufficiency,
)
from app.models.approval import (
    ApprovalStatus,
    EvidenceItemDecisionType,
    EvidenceReleaseDecision,
)
from app.models.evidence import (
    MissingEvidence,
    SanitizedEvidenceExcerpt,
    SensitivityStatus,
)
from app.models.plan import CollectorName
from app.security.sanitizer import sanitize_text

logger = logging.getLogger(__name__)


class _EvidenceReferenceProposal(StrictModel):
    artifact_id: str
    excerpt_id: str | None = None
    safe_excerpt: str | None = Field(default=None, max_length=2000)


class _EvidenceFindingProposal(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="other",min_length=1, max_length=100)
    confidence: ConfidenceLevel
    evidence_references: list[_EvidenceReferenceProposal] = Field(
        min_length=1,
        max_length=50,
    )
    unverified_assumptions: list[str] = Field(default_factory=list, max_length=20)
    suggested_next_step: str | None = Field(default=None, max_length=1000)


class _AnalysisProposal(StrictModel):
    issue_summary: str = Field(min_length=1, max_length=1000)
    findings: list[_EvidenceFindingProposal] = Field(default_factory=list, max_length=100)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list, max_length=50)
    evidence_sufficiency: EvidenceSufficiency
    supplemental_plan_required: bool
    warnings: list[str] = Field(default_factory=list, max_length=20)


_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9:/])(?:[A-Za-z]:\\[^\r\n\"<>|?*]+|"
    r"\\\\[^\\/\r\n]+\\[^\\\r\n]+|"
    r"/(?:[^\s/\"'<>]+/)*[^\s/\"'<>]+)",
    re.IGNORECASE,
)
_EXECUTABLE_PATTERN = re.compile(
    r"""(?im)(?:^\s*(?:[$>]\s*)?(?:powershell(?:\.exe)?|pwsh|cmd(?:\.exe)?|"""
    r"""bash|sh|zsh|python(?:\.exe)?|pip(?:3)?|curl|wget|invoke-expression|"""
    r"""invoke-webrequest|start-process|sudo|rm|remove-item|del|format|chmod|"""
    r"""chown|net(?:\.exe)?|sc(?:\.exe)?|systemctl|reg(?:\.exe)?|"""
    r"""ipconfig|tasklist|whoami|new-item|set-executionpolicy)\b|"""
    r"""\b(?:run|execute|type|enter)\s+(?:the\s+)?(?:command\s+)?"""
    r"""(?:[`'\"])?(?:powershell(?:\.exe)?|pwsh|cmd(?:\.exe)?|bash|sh|"""
    r"""python(?:\.exe)?|pip(?:3)?|curl|wget|invoke-expression|"""
    r"""invoke-webrequest|start-process|sudo|rm|remove-item|del|format|"""
    r"""chmod|chown|net(?:\.exe)?|sc(?:\.exe)?|systemctl|reg(?:\.exe)?|"""
    r"""ipconfig|tasklist|whoami|new-item|set-executionpolicy)\b|"""
    r"""\b(?:&&|\|\||;\s*(?:sudo|rm|del|powershell|pwsh|cmd|bash|sh)\b))"""
)


class EvidenceAnalysisService:
    """Request structured analysis without access to local tools or files."""

    def __init__(
        self,
        *,
        provider: ChatModelProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._provider = provider or GroqChatModelProvider()
        self._settings = settings or get_settings()

    @property
    def maximum_payload_bytes(self) -> int:
        return self._settings.analysis_payload_max_bytes

    async def analyze(
        self,
        workflow_id: UUID,
        payload: dict[str, Any],
    ) -> AnalysisResult:
        """Analyze only supplied payload data and validate all output references."""
        try:
            model = self._provider.structured_model(
                _AnalysisProposal,
                temperature=0.0,
                max_tokens=self._settings.groq_analysis_max_output_tokens,
            )
            result = await model.ainvoke(build_analysis_messages(payload))
            if isinstance(result, dict):
                field_count = len(result)
                findings = result.get("findings")
                missing_evidence = result.get("missing_evidence")
            elif isinstance(result, AnalysisResult):
                field_count = len(result.model_fields_set)
                findings = result.findings
                missing_evidence = result.missing_evidence
            else:
                field_count = 0
                findings = None
                missing_evidence = None
            logger.info(
                "Evidence analysis provider response received: "
                "type=%s, field_count=%d, findings_count=%s, "
                "missing_evidence_count=%s",
                type(result).__name__,
                field_count,
                len(findings) if isinstance(findings, list) else "unavailable",
                (
                    len(missing_evidence)
                    if isinstance(missing_evidence, list)
                    else "unavailable"
                ),
            )
            analysis = _validate_structured_result(result, workflow_id)
            logger.info(
                "Evidence analysis provider response validated: "
            )
            validate_analysis_result(
                analysis,
                workflow_id=workflow_id,
                approved_artifacts={
                    UUID(item["artifact_id"])
                    for item in payload["approved_excerpts"]
                },
                approved_excerpts={
                    UUID(item["excerpt_id"]): UUID(item["artifact_id"])
                    for item in payload["approved_excerpts"]
                },
            )

            logger.info(
                "Evidence analysis provider response references validated: "
            )


            return analysis
        except AIProviderError as error:
            logger.warning(
                "Evidence analysis provider request failed: %s (%s)",
                _failure_category(error),
                type(error).__name__,
            )
            logger.debug("Evidence analysis provider error details: %s", str(error))
            raise
        except Exception as error:
            category = _failure_category(error)
            logger.warning(
                "Evidence analysis provider request failed: %s (%s)",
                category,
                type(error).__name__,
            )
            logger.debug("Evidence analysis provider error details: %s", str(error))
            raise AIProviderError(_safe_message(category)) from None


def build_external_analysis_payload(
    *,
    workflow_id: UUID,
    issue_summary: str,
    decision: EvidenceReleaseDecision,
    excerpts: list[SanitizedEvidenceExcerpt],
    artifacts: list[Any],
    source_collectors: dict[str, str | None],
    missing_evidence: list[MissingEvidence],
    warnings: list[str],
    maximum_bytes: int,
) -> tuple[dict[str, Any], list[str]]:
    """Build a bounded payload exclusively from decision-approved excerpts."""
    if decision.workflow_id != workflow_id:
        raise ValueError("Evidence-release decision belongs to another workflow.")
    if not decision.reviewer_acknowledgement or not decision.allow_external_ai_analysis:
        raise ValueError("External analysis requires explicit reviewer approval.")
    if decision.status not in {
        ApprovalStatus.APPROVED,
        ApprovalStatus.PARTIALLY_APPROVED,
    }:
        raise ValueError("Evidence-release decision does not authorize analysis.")

    artifact_by_id = {str(item.id): item for item in artifacts}
    excerpt_by_id = {item.excerpt_id: item for item in excerpts}
    selected: list[dict[str, object]] = []
    excluded: set[UUID] = set()
    for item in decision.item_decisions:
        artifact = artifact_by_id.get(str(item.artifact_id))
        if (
            artifact is None
            or artifact.workflow_id != str(workflow_id)
            or not artifact.sanitized
            or artifact.sensitivity_status == SensitivityStatus.BLOCKED.value
        ):
            if item.approved_excerpt_ids:
                raise ValueError("Evidence-release decision includes blocked evidence.")
            if artifact is None:
                raise ValueError("Evidence-release decision references an unknown artifact.")
        excluded.update(item.excluded_excerpt_ids)
        if item.decision != EvidenceItemDecisionType.INCLUDE:
            if item.approved_excerpt_ids:
                raise ValueError("Excluded or revised evidence cannot be included.")
            continue
        for excerpt_id in item.approved_excerpt_ids:
            excerpt = excerpt_by_id.get(excerpt_id)
            if excerpt is None or excerpt.artifact_id != item.artifact_id:
                raise ValueError("Evidence-release decision references an unknown excerpt.")
            source_collector = source_collectors.get(str(artifact.id))
            if source_collector not in {collector.value for collector in CollectorName}:
                raise ValueError("Approved evidence has no supported source collector.")
            selected.append(
                {
                    "excerpt_id": str(excerpt.excerpt_id),
                    "artifact_id": str(artifact.id),
                    "artifact_type": artifact.artifact_type,
                    "source_collector": source_collector,
                    "content": excerpt.content,
                }
            )
    if not selected:
        raise ValueError("At least one sanitized excerpt must be approved.")
    selected_ids = {UUID(str(item["excerpt_id"])) for item in selected}
    if selected_ids & excluded:
        raise ValueError("An excluded excerpt cannot be included.")

    safe_summary, _ = sanitize_text(issue_summary)
    _validate_safe_text(safe_summary)
    safe_warnings = []
    for warning in warnings:
        cleaned, _ = sanitize_text(warning)
        _validate_safe_text(cleaned)
        safe_warnings.append(cleaned)
    payload: dict[str, Any] = {
        "issue_summary": safe_summary,
        "approved_excerpts": sorted(
            selected,
            key=lambda item: item["excerpt_id"],
        ),
        "missing_evidence": [
            item.model_dump(mode="json") for item in missing_evidence
        ],
        "interpretation_warnings": list(dict.fromkeys(safe_warnings)),
    }
    payload_warnings: list[str] = []
    while _payload_size(payload) > maximum_bytes and payload["approved_excerpts"]:
        largest = max(
            payload["approved_excerpts"],
            key=lambda item: len(str(item["content"])),
        )
        current = str(largest["content"])
        if len(current) > 64:
            largest["content"] = current[: max(64, len(current) * 3 // 4)]
            if "Payload size was reduced deterministically to fit the configured limit." not in payload_warnings:
                payload_warnings.append(
                    "Payload size was reduced deterministically to fit the configured limit."
                )
        elif current:
            largest["content"] = ""
            payload_warnings.append(
                "Approved excerpt content was fully truncated to fit the payload limit; traceability metadata was retained."
            )
        else:
            raise ValueError("Approved evidence metadata exceeds the analysis payload limit.")
    if not payload["approved_excerpts"] or _payload_size(payload) > maximum_bytes:
        raise ValueError("Approved evidence cannot fit within the analysis payload limit.")
    return payload, list(dict.fromkeys(payload_warnings))


def _payload_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _validate_structured_result(
    result: object,
    workflow_id: UUID,
) -> AnalysisResult:
    if result is None or result == "" or result == {}:
        raise AIProviderError(
            "The analysis provider returned an empty result."
        )

    try:
        if isinstance(result, AnalysisResult):
            proposal_data = result.model_dump(
                mode="json",
                exclude={"analysis_id", "workflow_id", "analyzed_at"},
            )
            for finding in proposal_data["findings"]:
                finding.pop("finding_id", None)
        else:
            proposal_data = result
        proposal = _AnalysisProposal.model_validate(proposal_data)
        analysis = AnalysisResult(
            workflow_id=workflow_id,
            issue_summary=proposal.issue_summary,
            findings=[
                EvidenceFinding(
                    title=finding.title,
                    description=finding.description,
                    category=finding.category,
                    confidence=finding.confidence,
                    evidence_references=[
                        EvidenceReference(
                            artifact_id=UUID(reference.artifact_id),
                            excerpt_id=(
                                UUID(reference.excerpt_id)
                                if reference.excerpt_id is not None
                                else None
                            ),
                            safe_excerpt=reference.safe_excerpt,
                        )
                        for reference in finding.evidence_references
                    ],
                    unverified_assumptions=finding.unverified_assumptions,
                    suggested_next_step=finding.suggested_next_step,
                )
                for finding in proposal.findings
            ],
            missing_evidence=proposal.missing_evidence,
            evidence_sufficiency=proposal.evidence_sufficiency,
            supplemental_plan_required=proposal.supplemental_plan_required,
            warnings=proposal.warnings,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        logger.warning("Analysis provider returned invalid structured output: %s", type(exc).__name__)
        raise AIProviderError(
            "The analysis provider returned invalid structured output."
        ) from exc

    logger.info("Analysis metadata and identifiers assigned locally")

    return analysis


def validate_analysis_result(
    analysis: AnalysisResult,
    *,
    workflow_id: UUID,
    approved_artifacts: set[UUID],
    approved_excerpts: dict[UUID, UUID],
) -> AnalysisResult:
    """Reject invented references, unsafe text, and unapproved evidence."""
    if analysis.workflow_id != workflow_id:
        raise ValueError("Analysis workflow does not match.")
    for value in [analysis.issue_summary, *analysis.warnings]:
        _validate_safe_text(value)
    if not approved_excerpts and analysis.findings:
        raise ValueError("Analysis findings require approved evidence.")
    for finding in analysis.findings:
        for value in (
            finding.title,
            finding.description,
            finding.category,
            *finding.unverified_assumptions,
        ):
            _validate_safe_text(value)
        if finding.suggested_next_step:
            _validate_safe_text(finding.suggested_next_step)
            if _EXECUTABLE_PATTERN.search(finding.suggested_next_step):
                raise ValueError("Analysis contains executable next steps.")
        if not finding.evidence_references:
            raise ValueError("Findings must reference approved evidence.")
        for reference in finding.evidence_references:
            if reference.artifact_id not in approved_artifacts:
                raise ValueError("Analysis referenced an unapproved artifact.")
            if (
                reference.excerpt_id is None
                or approved_excerpts.get(reference.excerpt_id)
                != reference.artifact_id
            ):
                raise ValueError("Analysis referenced an unapproved excerpt.")
            if reference.safe_excerpt is not None:
                raise ValueError("Analysis must reference excerpts by ID only.")
    supported_collectors = set(CollectorName)
    for missing in analysis.missing_evidence:
        if missing.collector not in supported_collectors:
            raise ValueError("Analysis contains an unsupported collector.")
        _validate_safe_text(missing.reason)
        _validate_safe_text(missing.impact)
    return analysis


def _validate_safe_text(value: str) -> None:
    validate_secret_free_text(value)
    if _PATH_PATTERN.search(value) or _EXECUTABLE_PATTERN.search(value):
        raise ValueError("Analysis contains unsafe text.")


def _failure_category(error: Exception) -> str:
    name = type(error).__name__.lower()
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in name:
        return "timeout"
    if "auth" in name or "permission" in name:
        return "authentication"
    if "ratelimit" in name or "rate_limit" in name:
        return "rate_limit"
    if any(fragment in name for fragment in ("internalserver", "servererror", "apierror")):
        return "provider_server"
    return "invalid_or_unavailable"


def _safe_message(category: str) -> str:
    return {
        "timeout": "The analysis provider request timed out.",
        "authentication": "The analysis provider rejected its configured credentials.",
        "rate_limit": "The analysis provider is temporarily rate limited.",
        "provider_server": "The analysis provider is temporarily unavailable.",
        "invalid_or_unavailable": "The analysis provider returned an invalid or unavailable result.",
    }[category]
