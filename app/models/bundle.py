"""Models describing verified diagnostic bundle metadata."""

import re
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.models._common import (
    StrictModel,
    UTCDateTime,
    is_windows_device_name,
    utc_now,
    validate_relative_path,
    validate_sha256,
)
from app.models.evidence import ArtifactType


class BundleVerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class ManifestEntry(StrictModel):
    """Metadata for one file represented in a future bundle manifest."""

    relative_path: str = Field(min_length=1, max_length=500)
    sha256: str
    size_bytes: int = Field(ge=0)
    artifact_type: ArtifactType

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        return validate_relative_path(path)

    @field_validator("sha256")
    @classmethod
    def validate_digest(cls, digest: str) -> str:
        validated = validate_sha256(digest)
        assert validated is not None
        return validated


class BundleMetadata(StrictModel):
    """Safe metadata for a candidate diagnostic ZIP bundle."""

    bundle_id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID
    filename: str = Field(min_length=1, max_length=200)
    relative_path: str = Field(min_length=1, max_length=500)
    manifest_relative_path: str | None = Field(default=None, max_length=500)
    verification_status: BundleVerificationStatus = BundleVerificationStatus.PENDING
    sanitized_only: bool
    created_at: UTCDateTime = Field(default_factory=utc_now)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, filename: str) -> str:
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", filename) is None
            or filename in {".", ".."}
            or ".." in filename
            or is_windows_device_name(filename)
        ):
            raise ValueError("Bundle filename is not safe.")
        return filename

    @field_validator("relative_path", "manifest_relative_path")
    @classmethod
    def validate_paths(cls, path: str | None) -> str | None:
        return validate_relative_path(path) if path is not None else None

    @model_validator(mode="after")
    def require_sanitized_verified_bundle(self) -> "BundleMetadata":
        if (
            self.verification_status == BundleVerificationStatus.VERIFIED
            and not self.sanitized_only
        ):
            raise ValueError("Only sanitized-only bundles may be verified.")
        return self
