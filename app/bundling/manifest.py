"""Deterministic, streamed SHA-256 manifests for candidate bundles."""

import hashlib
import json
from pathlib import Path

from app.models.bundle import ManifestEntry
from app.models.evidence import ArtifactType
from app.models._common import validate_relative_path

MANIFEST_FILENAME = "manifest.json"
_CHUNK_SIZE = 1024 * 1024


def generate_manifest(
    root: Path,
    file_types: dict[str, ArtifactType],
) -> list[ManifestEntry]:
    """Hash every regular candidate file except the manifest itself."""
    resolved_root = root.resolve(strict=True)
    candidate_files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Candidate bundles cannot contain symbolic links.")
        if not path.is_file():
            continue
        relative = validate_relative_path(path.relative_to(root).as_posix())
        if relative == MANIFEST_FILENAME:
            continue
        if relative not in file_types or relative in candidate_files:
            raise ValueError("Candidate bundle contains an unexpected or duplicate path.")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root):
            raise ValueError("Candidate file is outside its bundle directory.")
        candidate_files[relative] = path
    if set(candidate_files) != set(file_types):
        raise ValueError("One or more expected candidate files are missing.")

    entries: list[ManifestEntry] = []
    for relative in sorted(candidate_files):
        digest = hashlib.sha256()
        size = 0
        with candidate_files[relative].open("rb") as stream:
            while chunk := stream.read(_CHUNK_SIZE):
                size += len(chunk)
                digest.update(chunk)
        entries.append(
            ManifestEntry(
                relative_path=relative,
                artifact_type=file_types[relative],
                size_bytes=size,
                sha256=digest.hexdigest(),
            )
        )
    manifest_path = root / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(
            [entry.model_dump(mode="json") for entry in entries],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return entries
