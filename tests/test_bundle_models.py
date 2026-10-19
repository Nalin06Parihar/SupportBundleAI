"""Validation tests for bundle metadata contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.bundle import (
    BundleMetadata,
    BundleVerificationStatus,
    ManifestEntry,
)
from app.models.evidence import ArtifactType


def make_bundle(**overrides: object) -> BundleMetadata:
    values: dict[str, object] = {
        "workflow_id": uuid4(),
        "filename": "diagnostic-bundle.zip",
        "relative_path": "bundles/diagnostic-bundle.zip",
        "verification_status": BundleVerificationStatus.PENDING,
        "sanitized_only": True,
    }
    values.update(overrides)
    return BundleMetadata(**values)


def test_valid_bundle_metadata_is_accepted() -> None:
    assert make_bundle().filename == "diagnostic-bundle.zip"


@pytest.mark.parametrize("filename", ["../bundle.zip", "C:\\bundle.zip", "bad/name.zip"])
def test_unsafe_bundle_filename_is_rejected(filename: str) -> None:
    with pytest.raises(ValidationError):
        make_bundle(filename=filename)


@pytest.mark.parametrize(
    "path",
    ["/absolute/bundle.zip", "C:\\bundle.zip", "\\\\server\\bundle.zip", "../bundle.zip"],
)
def test_unsafe_bundle_path_is_rejected(path: str) -> None:
    with pytest.raises(ValidationError):
        make_bundle(relative_path=path)


def test_invalid_manifest_hash_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ManifestEntry(
            relative_path="evidence/data.json",
            sha256="invalid",
            size_bytes=5,
            artifact_type=ArtifactType.OTHER,
        )


def test_valid_manifest_hash_is_accepted() -> None:
    entry = ManifestEntry(
        relative_path="evidence/data.json",
        sha256="a" * 64,
        size_bytes=5,
        artifact_type=ArtifactType.OTHER,
    )
    assert entry.sha256 == "a" * 64


def test_verified_bundle_requires_sanitized_only_content() -> None:
    with pytest.raises(ValidationError):
        make_bundle(
            verification_status=BundleVerificationStatus.VERIFIED,
            sanitized_only=False,
        )
