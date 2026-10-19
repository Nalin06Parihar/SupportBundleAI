"""Shared validation helpers for domain and API models."""

import json
import math
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, cast

from pydantic import AwareDatetime, AfterValidator, BaseModel, ConfigDict

_WINDOWS_DEVICE_NAMES = {
    "con",
    "conin$",
    "conout$",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def is_windows_device_name(value: str) -> bool:
    """Return whether a filename component aliases a Windows device."""
    return value.split(".", maxsplit=1)[0].lower() in _WINDOWS_DEVICE_NAMES


class StrictModel(BaseModel):
    """Base for contracts that reject undeclared input fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def normalize_utc(value: datetime) -> datetime:
    """Normalize an aware datetime to UTC."""
    return value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AwareDatetime, AfterValidator(normalize_utc)]


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


def is_json_value(value: object) -> bool:
    """Return whether a value is composed only of finite JSON values."""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and is_json_value(item)
            for key, item in value.items()
        )
    return False


def validate_json_mapping(
    value: object,
    *,
    maximum_characters: int = 100_000,
) -> dict[str, object]:
    """Validate JSON-compatible mapping data and bound its serialized size."""
    if not isinstance(value, dict) or not is_json_value(value):
        raise ValueError("Value must contain JSON-compatible data.")
    try:
        serialized = json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError("Value must contain JSON-compatible data.") from None
    if len(serialized) > maximum_characters:
        raise ValueError("JSON data exceeds the allowed size.")
    return cast(dict[str, object], value)


def validate_secret_free_text(value: str) -> str:
    """Reject obvious inline credentials before text is written to persistence."""
    patterns = (
        r"(?i)\b(?:api[_-]?key|password|passwd|client[_-]?secret|"
        r"access[_-]?token|authorization)\s*[:=]\s*['\"]?[^'\"\s,;]{4,}",
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?<![A-Za-z0-9])(?:[A-Za-z]:\\|\\\\[^\\]+\\|/(?:Users|home|root|var|tmp|etc)/)",
    )
    if any(re.search(pattern, value) for pattern in patterns):
        raise ValueError("Text containing credential-like values cannot be persisted.")
    return value


def validate_persistable_metadata(value: dict[str, object]) -> dict[str, object]:
    """Reject likely content, secret, and execution fields in persisted metadata."""
    validate_json_mapping(value)
    reject_sensitive_parameter_names(value)
    prohibited_fragments = ("raw", "evidence", "content", "payload", "prompt", "binary")

    def inspect(item: object) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if any(fragment in normalized for fragment in prohibited_fragments):
                    raise ValueError("Metadata contains a prohibited content field.")
                inspect(nested)
        elif isinstance(item, list):
            for nested in item:
                inspect(nested)
        elif isinstance(item, str):
            if len(item) > 2000:
                raise ValueError("Metadata string exceeds the allowed size.")
            validate_secret_free_text(item)

    inspect(value)
    return value


def validate_warnings(value: list[str], *, maximum_count: int = 20) -> list[str]:
    """Validate a bounded list of safe, display-sized warning strings."""
    if len(value) > maximum_count:
        raise ValueError(f"At most {maximum_count} warnings are allowed.")
    if any(not warning or len(warning) > 500 for warning in value):
        raise ValueError("Warnings must contain between 1 and 500 characters.")
    return value


def validate_relative_path(value: str) -> str:
    """Normalize a safe relative path while rejecting absolute and traversal paths."""
    if not value or "\x00" in value:
        raise ValueError("Path must be a non-empty relative path.")

    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        raise ValueError("Absolute paths are not allowed.")
    if re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("Drive-qualified paths are not allowed.")
    if ":" in normalized:
        raise ValueError("Colon characters are not allowed in paths.")

    path = PurePosixPath(normalized)
    if path.is_absolute() or any(
        part == ".." or part.rstrip(" .") == ".." for part in path.parts
    ):
        raise ValueError("Path traversal is not allowed.")
    if any(part.endswith((" ", ".")) for part in path.parts):
        raise ValueError("Path components cannot end with spaces or dots.")
    if any(is_windows_device_name(part) for part in path.parts):
        raise ValueError("Windows device names are not allowed in paths.")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ValueError("Path must identify a relative file.")
    return normalized


def validate_sha256(value: str | None) -> str | None:
    """Validate a SHA-256 hex digest when one is supplied."""
    if value is not None and re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("SHA-256 must contain exactly 64 hexadecimal characters.")
    return value.lower() if value is not None else None


def reject_sensitive_parameter_names(value: dict[str, object]) -> dict[str, object]:
    """Reject secret-bearing and arbitrary-execution parameter names recursively."""
    prohibited_fragments = (
        "apikey",
        "password",
        "passwd",
        "token",
        "authorization",
        "privatekey",
        "secret",
        "credential",
        "command",
        "script",
        "executable",
        "powershell",
        "shell",
        "exec",
    )

    def inspect(item: object) -> None:
        if isinstance(item, dict):
            for key, nested_value in item.items():
                normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized_key == "code" or any(
                    fragment in normalized_key for fragment in prohibited_fragments
                ):
                    raise ValueError("Parameters contain a prohibited field name.")
                inspect(nested_value)
        elif isinstance(item, list):
            for nested_value in item:
                inspect(nested_value)

    inspect(value)
    return value
