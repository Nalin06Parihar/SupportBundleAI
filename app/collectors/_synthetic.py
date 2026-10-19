"""Bounded readers and artifact metadata helpers for synthetic scenario files."""

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID

from app.collectors.base import EvidenceUnavailable
from app.core.config import get_settings
from app.core.exceptions import PolicyViolationError
from app.models.evidence import ArtifactMetadata, ArtifactType, SensitivityStatus
from app.models.scenario import ScenarioDefinition
from app.scenarios.loader import ScenarioError, ScenarioLoader

MAX_LOG_LINES = 100
MAX_LOG_LINE_CHARACTERS = 900
MAX_CONFIG_CHARACTERS = 90_000


def find_evidence_file(
    loader: ScenarioLoader,
    scenario: ScenarioDefinition,
    extension: str,
) -> tuple[str, Path]:
    """Resolve the first declared reference with the requested extension."""
    for reference in scenario.evidence_files:
        if (
            not reference
            or "\x00" in reference
            or PurePosixPath(reference).is_absolute()
            or PureWindowsPath(reference).is_absolute()
            or PureWindowsPath(reference).drive
            or any(part == ".." for part in reference.replace("\\", "/").split("/"))
        ):
            raise PolicyViolationError("Scenario contains an unsafe evidence reference.")
        if Path(reference).suffix.lower() != extension:
            continue
        try:
            return reference.replace("\\", "/"), loader.resolve_evidence_file(reference)
        except ScenarioError:
            raise EvidenceUnavailable(
                "A declared synthetic evidence file is unavailable."
            ) from None
    raise EvidenceUnavailable("This scenario does not include the requested evidence file.")


def read_json_config(
    loader: ScenarioLoader,
    scenario: ScenarioDefinition,
) -> tuple[dict[str, Any], str, int]:
    """Read one declared, bounded JSON configuration file."""
    relative_path, file_path = find_evidence_file(loader, scenario, ".json")
    contents = read_bounded_bytes(file_path)
    try:
        text = contents.decode("utf-8")
        if len(text) > MAX_CONFIG_CHARACTERS:
            raise EvidenceUnavailable("Synthetic configuration exceeds the result size limit.")
        value: Any = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceUnavailable("Synthetic configuration is unavailable or invalid.") from None
    if not isinstance(value, dict):
        raise EvidenceUnavailable("Synthetic configuration has an unsupported structure.")
    return value, relative_path, len(contents)


def read_log_lines(
    loader: ScenarioLoader,
    scenario: ScenarioDefinition,
    requested_limit: object,
) -> tuple[list[str], str, int, list[str]]:
    """Read a bounded number and size of lines from one declared log file."""
    relative_path, file_path = find_evidence_file(loader, scenario, ".log")
    contents = read_bounded_bytes(file_path)
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise EvidenceUnavailable("Synthetic log is unavailable or invalid.") from None

    line_limit = MAX_LOG_LINES
    if requested_limit is not None:
        if (
            isinstance(requested_limit, bool)
            or not isinstance(requested_limit, int)
            or not 1 <= requested_limit <= MAX_LOG_LINES
        ):
            raise PolicyViolationError("Log line limit is outside the allowed range.")
        line_limit = requested_limit

    lines = text.splitlines()
    warnings = []
    if len(lines) > line_limit:
        warnings.append("Log output was limited to the approved line count.")
    bounded_lines = [line[:MAX_LOG_LINE_CHARACTERS] for line in lines[:line_limit]]
    if any(len(line) > MAX_LOG_LINE_CHARACTERS for line in lines[:line_limit]):
        warnings.append("One or more log lines were shortened to the approved size.")
    return bounded_lines, relative_path, len(contents), warnings


def read_bounded_bytes(path: Path) -> bytes:
    """Read a small file only after checking the configured size bound."""
    maximum_size = int(get_settings().max_log_file_size_mb * 1024 * 1024)
    try:
        size = path.stat().st_size
        if size > maximum_size:
            raise EvidenceUnavailable("Synthetic evidence file exceeds the configured size limit.")
        with path.open("rb") as file:
            contents = file.read(maximum_size + 1)
    except EvidenceUnavailable:
        raise
    except OSError:
        raise EvidenceUnavailable("A declared synthetic evidence file is unavailable.") from None
    if len(contents) > maximum_size:
        raise EvidenceUnavailable("Synthetic evidence file exceeds the configured size limit.")
    return contents


def file_artifact(
    *,
    workflow_id: UUID,
    relative_path: str,
    size_bytes: int,
    artifact_type: ArtifactType,
    media_type: str,
) -> ArtifactMetadata:
    """Describe an unsanitized scenario file using only its controlled relative path."""
    return ArtifactMetadata(
        workflow_id=workflow_id,
        name=PurePosixPath(relative_path).name,
        artifact_type=artifact_type,
        relative_path=relative_path,
        media_type=media_type,
        size_bytes=size_bytes,
        sanitized=False,
        sensitivity_status=SensitivityStatus.NOT_SCANNED,
    )


def synthetic_host(configuration: dict[str, Any]) -> dict[str, object]:
    host = configuration.get("synthetic_host")
    return host if isinstance(host, dict) else {}
