"""Deterministic local sanitization and residual-risk classification."""

import ipaddress
import hashlib
import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid5

from app.core.config import Settings, get_settings
from app.core.exceptions import SanitizationError
from app.models._common import validate_json_mapping, validate_relative_path
from app.models.evidence import (
    ArtifactMetadata,
    ArtifactType,
    RedactionCategory,
    RedactionCount,
    ResidualRiskFlag,
    ResidualRiskLevel,
    SanitizationReport,
    SanitizationStatus,
    SanitizedEvidenceExcerpt,
    SensitivityStatus,
)
from app.repositories.artifacts import ArtifactRepository
from app.scenarios.loader import ScenarioError, ScenarioLoader

MAX_EXCERPT_CHARACTERS = 8_000
_MARKER = re.compile(r"\[REDACTED_[A-Z_]+\]")
_REDACTIONS: tuple[tuple[RedactionCategory, re.Pattern[str], str], ...] = (
    (
        RedactionCategory.PRIVATE_KEY,
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        RedactionCategory.CONNECTION_STRING,
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql|oracle)://"
            r"[^\s\"'<>]+|"
            r"\b(?:server|data source)\s*=\s*[^\r\n]+",
            re.IGNORECASE,
        ),
        "[REDACTED_CONNECTION_STRING]",
    ),
    (
        RedactionCategory.AUTHORIZATION_HEADER,
        re.compile(
            r"(?im)\b(authorization\s*[:=]\s*)(?:bearer\s+\S+|basic\s+\S+|[^\r\n]+)"
        ),
        "[REDACTED_AUTHORIZATION]",
    ),
    (
        RedactionCategory.API_KEY,
        re.compile(
            r"(?i)\b(?:gsk_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{12,}|"
            r"gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
            r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
            r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
        ),
        "[REDACTED_SECRET]",
    ),
    (
        RedactionCategory.CREDENTIAL,
        re.compile(
            r"""(?im)\b[\w.-]*(?:password|passwd|pwd|secret|token|credential|"""
            r"""api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key)"""
            r"""[\w.-]*\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}\]]+)"""
        ),
        "[REDACTED_SECRET]",
    ),
    (
        RedactionCategory.EMAIL,
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    (
        RedactionCategory.BUSINESS_CONTEXT,
        re.compile(r"(?i)\bhttps?://[^\s\"'<>]+"),
        "[REDACTED_BUSINESS_CONTEXT]",
    ),
    (
        RedactionCategory.USER_PATH,
        re.compile(r"\\\\[^\\/\s]+\\[^\s\"'<>|?*\[\]]+"),
        "[REDACTED_PATH]",
    ),
    (
        RedactionCategory.USER_PATH,
        re.compile(r"(?i)\b[A-Z]:\\[^\r\n\"<>|?*\[\]]+"),
        "[REDACTED_PATH]",
    ),
    (
        RedactionCategory.USER_PATH,
        re.compile(r"(?<![\w:])/(?:[^/\s\"'<>[\]]+/)*[^/\s\"'<>[\]]*"),
        "[REDACTED_PATH]",
    ),
)
_IP_ADDRESS = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_ADDRESS = re.compile(
    r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:%[\w.-]+)?(?![\w:])"
)
_SENSITIVE_KEYS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "apikey",
    "authorization",
    "privatekey",
    "credential",
    "connectionstring",
    "databaseurl",
    "accesskey",
    "cookie",
    "setcookie",
)
_CONTEXT_VALUE = re.compile(
    r"""(?im)\b(customer|tenant|project|dataset|account|organization|"""
    r"""organisation|environment|hostname|host|url)(?:[_ -](?:name|id))?"""
    r"""\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}\]]+)"""
)
_RISK_KEYS: tuple[tuple[str, RedactionCategory], ...] = (
    ("customer", RedactionCategory.CUSTOMER_IDENTIFIER),
    ("tenant", RedactionCategory.TENANT_IDENTIFIER),
    ("project", RedactionCategory.INTERNAL_PROJECT_NAME),
    ("dataset", RedactionCategory.BUSINESS_CONTEXT),
    ("account", RedactionCategory.BUSINESS_CONTEXT),
    ("organization", RedactionCategory.BUSINESS_CONTEXT),
    ("organisation", RedactionCategory.BUSINESS_CONTEXT),
    ("environment", RedactionCategory.BUSINESS_CONTEXT),
    ("hostname", RedactionCategory.BUSINESS_CONTEXT),
    ("host", RedactionCategory.BUSINESS_CONTEXT),
    ("url", RedactionCategory.BUSINESS_CONTEXT),
)


class EvidenceSanitizationService:
    """Sanitize local artifact files into a separate workflow output directory."""

    def __init__(
        self,
        artifacts: ArtifactRepository,
        *,
        scenario_loader: ScenarioLoader,
        settings: Settings | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._scenario_loader = scenario_loader
        self._settings = settings or get_settings()
        self._output_root = Path(self._settings.output_directory).resolve()

    def stage_collector_data(
        self,
        *,
        workflow_id: UUID,
        step_id: UUID,
        data: dict[str, object],
    ) -> ArtifactMetadata | None:
        """Write bounded structured result data locally, not into state or SQLite."""
        if not data:
            return None
        try:
            safe_shape = validate_json_mapping(
                data,
                maximum_characters=self._maximum_file_size,
            )
            contents = json.dumps(
                safe_shape,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(contents) > self._maximum_file_size:
                raise SanitizationError("Collector output exceeds the local size limit.")
            relative_path = f"workflows/{workflow_id}/collected/{step_id}.json"
            destination = self._resolve_output(relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                with destination.open("xb") as file:
                    file.write(contents)
            return ArtifactMetadata(
                artifact_id=uuid5(workflow_id, f"collected-data:{step_id}"),
                workflow_id=workflow_id,
                name=f"{step_id}.json",
                artifact_type=ArtifactType.OTHER,
                relative_path=relative_path,
                media_type="application/json",
                size_bytes=destination.stat().st_size,
                sanitized=False,
                sensitivity_status=SensitivityStatus.NOT_SCANNED,
            )
        except SanitizationError:
            raise
        except (OSError, TypeError, ValueError):
            raise SanitizationError(
                "Collector output could not be staged safely for sanitization."
            ) from None

    def sanitize_workflow(
        self,
        workflow_id: UUID,
    ) -> tuple[SanitizationReport, list[SanitizedEvidenceExcerpt], list[ArtifactMetadata]]:
        """Sanitize currently-unsanitized artifacts and return only safe report data."""
        records = self._artifacts.list_for_workflow(workflow_id)
        sanitized_ids = {
            item.id for item in records if item.sanitized
        }
        previously_blocked = [
            item
            for item in records
            if not item.sanitized
            and item.sensitivity_status == SensitivityStatus.BLOCKED.value
        ]
        source_records = [
            item
            for item in records
            if not item.sanitized
            and item.sensitivity_status != SensitivityStatus.BLOCKED.value
            and str(uuid5(UUID(item.id), "sanitized-v1"))
            not in sanitized_ids
        ]
        reports = [
            self._sanitize_record(workflow_id, item)
            for item in source_records
        ]
        counts: Counter[RedactionCategory] = Counter()
        flags: list[ResidualRiskFlag] = []
        excerpts: list[SanitizedEvidenceExcerpt] = []
        artifacts: list[ArtifactMetadata] = [
            self._artifact_metadata(item)
            for item in records
            if item.sanitized
        ]
        approved_ids: list[UUID] = []
        blocked_ids: list[UUID] = [UUID(item.id) for item in previously_blocked]
        warnings: list[str] = []
        if previously_blocked:
            warnings.append("Previously blocked artifacts remain excluded from review.")
        for report in reports:
            counts.update(report["counts"])
            flags.extend(report["flags"])
            artifacts.append(report["metadata"])
            if report["blocked"]:
                blocked_ids.append(report["metadata"].artifact_id)
                warnings.append(report["warning"])
            else:
                approved_ids.append(report["metadata"].artifact_id)
                if report["excerpt"] is not None:
                    excerpts.append(report["excerpt"])
                if report["warning"]:
                    warnings.append(report["warning"])

        if not reports and not previously_blocked:
            warnings.append("No unsanitized artifacts were available for processing.")
            status = SanitizationStatus.PARTIAL
        elif blocked_ids and approved_ids:
            status = SanitizationStatus.PARTIAL
        elif blocked_ids:
            status = SanitizationStatus.BLOCKED
        else:
            status = SanitizationStatus.COMPLETED
        return (
            SanitizationReport(
                report_id=uuid5(workflow_id, "sanitization-report-v1"),
                workflow_id=workflow_id,
                status=status,
                files_processed=len(reports) + len(previously_blocked),
                total_redactions=sum(counts.values()),
                redactions_by_category=[
                    RedactionCount(category=category, count=count)
                    for category, count in sorted(
                        counts.items(),
                        key=lambda entry: entry[0].value,
                    )
                ],
                residual_risk_flags=list(
                    {flag.flag_id: flag for flag in flags}.values()
                )[:500],
                approved_artifact_ids=approved_ids,
                blocked_artifact_ids=blocked_ids,
                warnings=list(dict.fromkeys(warnings))[:20],
            ),
            excerpts,
            artifacts,
        )

    @staticmethod
    def _artifact_metadata(record: Any) -> ArtifactMetadata:
        return ArtifactMetadata(
            artifact_id=UUID(record.id),
            workflow_id=UUID(record.workflow_id),
            name=record.name,
            artifact_type=ArtifactType(record.artifact_type),
            relative_path=record.relative_path,
            media_type=record.media_type,
            size_bytes=record.size_bytes,
            sanitized=record.sanitized,
            sensitivity_status=SensitivityStatus(record.sensitivity_status),
            sha256=record.sha256,
            created_at=record.created_at,
        )

    def verify_artifact(self, artifact: ArtifactMetadata) -> bool:
        """Verify the persisted sanitized copy is confined, bounded, and redacted."""
        if not artifact.sanitized or artifact.sensitivity_status == SensitivityStatus.BLOCKED:
            return False
        try:
            path = self._resolve_output(artifact.relative_path, strict=True)
            contents = path.read_bytes()
            if len(contents) > self._maximum_file_size:
                return False
            if artifact.sha256 and hashlib.sha256(contents).hexdigest() != artifact.sha256:
                return False
            text = contents.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError, SanitizationError):
            return False
        return not _contains_unredacted_sensitive_value(text)

    def _sanitize_record(self, workflow_id: UUID, record: Any) -> dict[str, Any]:
        source_id = UUID(record.id)
        output_id = uuid5(source_id, "sanitized-v1")
        suffix = PurePosixPath(record.relative_path).suffix.lower()
        relative_path = f"workflows/{workflow_id}/sanitized/{output_id}{suffix}"
        counts: Counter[RedactionCategory] = Counter()
        flags: list[ResidualRiskFlag] = []
        output_path: Path | None = None
        try:
            contents = self._read_source(record.relative_path)
            extension = PurePosixPath(record.relative_path).suffix.lower()
            if extension == ".json" or "json" in record.media_type.lower():
                source = json.loads(contents.decode("utf-8"))
                clean, counts, flags = _sanitize_structure(source, output_id)
                sanitized = json.dumps(
                    clean,
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    indent=2,
                )
            elif extension == ".log" or record.media_type.lower().startswith("text/"):
                source_text = contents.decode("utf-8")
                sanitized, counts = sanitize_text(source_text)
                flags = _classify_context(source_text, output_id)
            else:
                raise SanitizationError("Artifact format is unsupported.")
            encoded = sanitized.encode("utf-8")
            if len(encoded) > self._maximum_file_size:
                raise SanitizationError("Sanitized artifact exceeds the local size limit.")
            output_path = self._resolve_output(relative_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if not output_path.exists():
                with output_path.open("xb") as file:
                    file.write(encoded)
            if output_path.resolve(strict=True) == self._resolve_source(record.relative_path):
                raise SanitizationError("Sanitized output cannot replace its source.")
            metadata = ArtifactMetadata(
                artifact_id=output_id,
                workflow_id=workflow_id,
                name=f"{output_id}{suffix}",
                artifact_type=record.artifact_type,
                relative_path=relative_path,
                media_type=record.media_type,
                size_bytes=output_path.stat().st_size,
                sanitized=True,
                sensitivity_status=SensitivityStatus.AWAITING_HUMAN_REVIEW,
                sha256=hashlib.sha256(encoded).hexdigest(),
            )
            if not self.verify_artifact(metadata):
                _remove_unsafe_output(output_path)
                return self._blocked(record, counts)
            excerpt_id = uuid5(output_id, "excerpt:0")
            flags = [
                flag.model_copy(update={"excerpt_id": excerpt_id})
                for flag in flags
            ]
            excerpt_text = output_path.read_text(encoding="utf-8")
            shortened = len(excerpt_text) > MAX_EXCERPT_CHARACTERS
            excerpt = (
                SanitizedEvidenceExcerpt(
                    excerpt_id=excerpt_id,
                    artifact_id=output_id,
                    sequence_number=0,
                    content=excerpt_text[:MAX_EXCERPT_CHARACTERS],
                    redaction_count=sum(counts.values()),
                    residual_risk_level=_highest_risk(flags),
                )
                if excerpt_text
                else None
            )
            return {
                "metadata": metadata,
                "excerpt": excerpt,
                "counts": counts,
                "flags": flags,
                "blocked": False,
                "warning": (
                    "A sanitized excerpt was shortened for review."
                    if shortened
                    else ""
                ),
            }
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, SanitizationError):
            if output_path is not None and output_path.exists():
                _remove_unsafe_output(output_path)
            metadata = ArtifactMetadata(
                artifact_id=output_id,
                workflow_id=workflow_id,
                name=f"{output_id}{suffix or '.bin'}",
                artifact_type=record.artifact_type,
                relative_path=relative_path,
                media_type=record.media_type,
                size_bytes=0,
                sanitized=False,
                sensitivity_status=SensitivityStatus.BLOCKED,
            )
            return self._blocked(record, counts)

    def _blocked(
        self,
        record: Any,
        counts: Counter[RedactionCategory],
    ) -> dict[str, Any]:
        source_id = UUID(record.id)
        self._artifacts.update_sensitivity(source_id, SensitivityStatus.BLOCKED)
        blocked = ArtifactMetadata(
            artifact_id=source_id,
            workflow_id=UUID(record.workflow_id),
            name=record.name,
            artifact_type=ArtifactType(record.artifact_type),
            relative_path=record.relative_path,
            media_type=record.media_type,
            size_bytes=record.size_bytes,
            sanitized=False,
            sensitivity_status=SensitivityStatus.BLOCKED,
            sha256=record.sha256,
            created_at=record.created_at,
        )
        return {
            "metadata": blocked,
            "excerpt": None,
            "counts": counts,
            "flags": [],
            "blocked": True,
            "warning": "An artifact could not be sanitized safely and was blocked.",
        }

    def _read_source(self, relative_path: str) -> bytes:
        path = self._resolve_source(relative_path)
        try:
            with path.open("rb") as file:
                contents = file.read(self._maximum_file_size + 1)
        except OSError:
            raise SanitizationError("Artifact could not be read safely.") from None
        if len(contents) > self._maximum_file_size:
            raise SanitizationError("Artifact exceeds the local size limit.")
        return contents

    def _resolve_source(self, relative_path: str) -> Path:
        normalized = validate_relative_path(relative_path)
        try:
            return self._scenario_loader.resolve_evidence_file(normalized)
        except ScenarioError:
            return self._resolve_output(normalized, strict=True)

    def _resolve_output(self, relative_path: str, *, strict: bool = False) -> Path:
        normalized = validate_relative_path(relative_path)
        path = self._output_root.joinpath(
            *PurePosixPath(normalized).parts
        ).resolve(strict=strict)
        if not path.is_relative_to(self._output_root):
            raise SanitizationError("Artifact path is outside the controlled output directory.")
        if strict and not path.is_file():
            raise SanitizationError("Artifact is not an available controlled file.")
        return path

    @property
    def _maximum_file_size(self) -> int:
        return max(1, int(self._settings.max_log_file_size_mb * 1024 * 1024))


def sanitize_text(value: str) -> tuple[str, Counter[RedactionCategory]]:
    """Redact known credential and identifying patterns without altering input."""
    counts: Counter[RedactionCategory] = Counter()

    def redact_context(match: re.Match[str]) -> str:
        raw = match.group()
        if _MARKER.search(raw):
            return raw
        category = _risk_category(match.group(1))
        counts[category] += 1
        return _risk_marker(category)

    value = _CONTEXT_VALUE.sub(redact_context, value)
    for category, pattern, marker in _REDACTIONS:
        value, redacted = _substitute_unmarked(pattern, value, marker)
        counts[category] += redacted

    def redact_ip(match: re.Match[str]) -> str:
        try:
            ipaddress.IPv4Address(match.group())
        except ipaddress.AddressValueError:
            return match.group()
        counts[RedactionCategory.IP_ADDRESS] += 1
        return "[REDACTED_IP]"

    value = _IP_ADDRESS.sub(redact_ip, value)

    def redact_ipv6(match: re.Match[str]) -> str:
        address = match.group().split("%", maxsplit=1)[0]
        try:
            ipaddress.IPv6Address(address)
        except ipaddress.AddressValueError:
            return match.group()
        counts[RedactionCategory.IP_ADDRESS] += 1
        return "[REDACTED_IP]"

    value = _IPV6_ADDRESS.sub(redact_ipv6, value)
    return value, +counts


def _sanitize_structure(
    value: Any,
    artifact_id: UUID,
    path: tuple[str, ...] = (),
) -> tuple[Any, Counter[RedactionCategory], list[ResidualRiskFlag]]:
    counts: Counter[RedactionCategory] = Counter()
    flags: list[ResidualRiskFlag] = []
    if isinstance(value, dict):
        clean: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SanitizationError("Structured artifact contains an invalid key.")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            risk_category = next(
                (
                    category
                    for label, category in _RISK_KEYS
                    if label in normalized
                ),
                None,
            )
            if risk_category is not None and item not in (None, "", "[REDACTED_SECRET]"):
                flags.append(
                    _risk_flag(artifact_id, risk_category, path + (key,))
                )
                clean[key] = _risk_marker(risk_category)
                counts[risk_category] += 1
                continue
            if any(fragment in normalized for fragment in _SENSITIVE_KEYS):
                category = _category_for_key(normalized)
                clean[key] = (
                    "[REDACTED_AUTHORIZATION]"
                    if category == RedactionCategory.AUTHORIZATION_HEADER
                    else "[REDACTED_SECRET]"
                )
                counts[category] += 1
                continue
            nested, nested_counts, nested_flags = _sanitize_structure(
                item,
                artifact_id,
                path + (key,),
            )
            clean[key] = nested
            counts.update(nested_counts)
            flags.extend(nested_flags)
        return clean, counts, flags
    if isinstance(value, list):
        clean_list = []
        for index, item in enumerate(value):
            nested, nested_counts, nested_flags = _sanitize_structure(
                item,
                artifact_id,
                path + (str(index),),
            )
            clean_list.append(nested)
            counts.update(nested_counts)
            flags.extend(nested_flags)
        return clean_list, counts, flags
    if isinstance(value, str):
        clean_text, nested_counts = sanitize_text(value)
        counts.update(nested_counts)
        if re.search(r"\b[A-Za-z0-9_-]{24,}\b", value):
            flags.append(
                ResidualRiskFlag(
                    flag_id=uuid5(
                        artifact_id,
                        f"risk:high-entropy:{'/'.join(path)}",
                    ),
                    artifact_id=artifact_id,
                    category=RedactionCategory.OTHER,
                    risk_level=ResidualRiskLevel.LOW,
                    safe_description="An identifier-like value may require human review.",
                )
            )
        return clean_text, counts, flags
    if value is None or isinstance(value, (bool, int, float)):
        return value, counts, flags
    raise SanitizationError("Structured artifact contains an unsupported value.")


def _substitute_unmarked(
    pattern: re.Pattern[str],
    value: str,
    marker: str,
) -> tuple[str, int]:
    markers = list(_MARKER.finditer(value))
    parts: list[str] = []
    cursor = 0
    count = 0
    for match in pattern.finditer(value):
        if any(item.start() < match.end() and item.end() > match.start() for item in markers):
            continue
        parts.extend((value[cursor : match.start()], marker))
        cursor = match.end()
        count += 1
    if not count:
        return value, 0
    parts.append(value[cursor:])
    return "".join(parts), count


def _contains_unredacted_sensitive_value(value: str) -> bool:
    remaining = _MARKER.sub("", value)
    if "-----BEGIN" in remaining and "PRIVATE KEY" in remaining:
        return True
    if any(pattern.search(remaining) for _, pattern, _ in _REDACTIONS):
        return True
    if _IP_ADDRESS.search(remaining) or _IPV6_ADDRESS.search(remaining):
        return True
    return re.search(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        remaining,
    ) is not None


def _classify_context(value: str, artifact_id: UUID) -> list[ResidualRiskFlag]:
    flags: list[ResidualRiskFlag] = []
    for label, category in _RISK_KEYS:
        if re.search(rf"(?i)\b{re.escape(label)}(?:[_ -](?:name|id))?\b", value):
            flags.append(_risk_flag(artifact_id, category, (label,)))
    if re.search(r"(?i)\bhttps?://", value):
        flags.append(
            _risk_flag(
                artifact_id,
                RedactionCategory.BUSINESS_CONTEXT,
                ("url",),
            )
        )
    if re.search(r"\b[A-Za-z0-9_-]{24,}\b", value):
        flags.append(
            ResidualRiskFlag(
                flag_id=uuid5(artifact_id, "risk:high-entropy"),
                artifact_id=artifact_id,
                category=RedactionCategory.OTHER,
                risk_level=ResidualRiskLevel.LOW,
                safe_description="An identifier-like value may require human review.",
            )
        )
    return flags


def _risk_flag(
    artifact_id: UUID,
    category: RedactionCategory,
    path: tuple[str, ...],
) -> ResidualRiskFlag:
    safe_descriptions = {
        RedactionCategory.CUSTOMER_IDENTIFIER: "A customer-labeled value may require human review.",
        RedactionCategory.TENANT_IDENTIFIER: "A tenant-labeled value may require human review.",
        RedactionCategory.INTERNAL_PROJECT_NAME: "An internal project label may require human review.",
        RedactionCategory.BUSINESS_CONTEXT: "An organizational or environment value may require human review.",
    }
    safe_key = "/".join(path)
    return ResidualRiskFlag(
        flag_id=uuid5(artifact_id, f"risk:{category.value}:{safe_key}"),
        artifact_id=artifact_id,
        category=category,
        risk_level=ResidualRiskLevel.MEDIUM,
        safe_description=safe_descriptions.get(
            category,
            "Contextual sensitivity may require human review.",
        ),
    )


def _category_for_key(key: str) -> RedactionCategory:
    if "authorization" in key:
        return RedactionCategory.AUTHORIZATION_HEADER
    if "apikey" in key or "accesskey" in key:
        return RedactionCategory.API_KEY
    if "connectionstring" in key or "databaseurl" in key:
        return RedactionCategory.CONNECTION_STRING
    return RedactionCategory.CREDENTIAL


def _risk_category(label: str) -> RedactionCategory:
    normalized = label.lower()
    return next(
        category
        for fragment, category in _RISK_KEYS
        if fragment == normalized
    )


def _risk_marker(category: RedactionCategory) -> str:
    return f"[REDACTED_{category.value.upper()}]"


def _highest_risk(flags: list[ResidualRiskFlag]) -> ResidualRiskLevel:
    order = list(ResidualRiskLevel)
    return max((flag.risk_level for flag in flags), key=order.index) if flags else ResidualRiskLevel.NONE


def _remove_unsafe_output(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        raise SanitizationError(
            "Unsafe intermediate output could not be removed."
        ) from None
