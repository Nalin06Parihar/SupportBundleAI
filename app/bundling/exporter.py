"""Create and independently verify candidate diagnostic ZIP files."""

import hashlib
import json
import stat
import zipfile
from pathlib import Path

from app.bundling.manifest import MANIFEST_FILENAME, _CHUNK_SIZE
from app.models._common import validate_relative_path
from app.models.bundle import ManifestEntry


def create_candidate_zip(candidate_root: Path, archive_path: Path) -> None:
    """Write a ZIP containing only the candidate directory's regular files."""
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in sorted(candidate_root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Candidate bundles cannot contain symbolic links.")
            if path.is_file():
                relative = validate_relative_path(
                    path.relative_to(candidate_root).as_posix()
                )
                archive.write(path, arcname=relative)


def verify_candidate_zip(
    candidate_root: Path,
    archive_path: Path,
    manifest_entries: list[ManifestEntry],
    *,
    required_files: set[str],
    approved_artifacts: set[str],
) -> None:
    """Reopen, inspect, and hash-check every member against the manifest."""
    expected = {entry.relative_path: entry for entry in manifest_entries}
    if len(expected) != len(manifest_entries):
        raise ValueError("Manifest contains duplicate paths.")
    if MANIFEST_FILENAME in expected:
        raise ValueError("Manifest must not hash itself.")
    expected_members = set(expected) | {MANIFEST_FILENAME}
    if not required_files.issubset(expected):
        raise ValueError("Required bundle files are missing.")
    artifact_paths = {path for path in expected if path.startswith("artifacts/")}
    if artifact_paths != approved_artifacts:
        raise ValueError("Bundle artifact membership does not match approved artifacts.")
    if any(
        path.startswith("artifacts/") and expected[path].relative_path != path
        for path in artifact_paths
    ):
        raise ValueError("Bundle artifact path is invalid.")

    candidate_paths: set[str] = set()
    for path in candidate_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Candidate bundles cannot contain symbolic links.")
        if path.is_file():
            candidate_paths.add(
                validate_relative_path(path.relative_to(candidate_root).as_posix())
            )
    if candidate_paths != expected_members:
        raise ValueError("Candidate directory contains unexpected or missing files.")

    with zipfile.ZipFile(archive_path, mode="r") as archive:
        infos = archive.infolist()
        if any(
            info.is_dir()
            or stat.S_ISLNK(info.external_attr >> 16)
            for info in infos
        ):
            raise ValueError("ZIP contains a directory or symbolic-link member.")
        members = [info.filename for info in infos]
        safe_members = [validate_relative_path(name) for name in members]
        if len(safe_members) != len(set(safe_members)):
            raise ValueError("ZIP contains duplicate member paths.")
        if set(safe_members) != expected_members:
            raise ValueError("ZIP contains unexpected or missing files.")
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity check failed.")

        manifest_bytes = archive.read(MANIFEST_FILENAME)
        raw_manifest = json.loads(manifest_bytes)
        parsed = [ManifestEntry.model_validate(item) for item in raw_manifest]
        if parsed != manifest_entries:
            raise ValueError("ZIP manifest does not match the verified manifest.")
        for entry in parsed:
            digest = hashlib.sha256()
            size = 0
            with archive.open(entry.relative_path, mode="r") as stream:
                while chunk := stream.read(_CHUNK_SIZE):
                    size += len(chunk)
                    digest.update(chunk)
            if size != entry.size_bytes or digest.hexdigest() != entry.sha256:
                raise ValueError("ZIP member does not match its manifest entry.")

    for relative, entry in expected.items():
        source = candidate_root / relative
        if source.is_symlink():
            raise ValueError("Candidate bundles cannot contain symbolic links.")
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream:
            while chunk := stream.read(_CHUNK_SIZE):
                size += len(chunk)
                digest.update(chunk)
        if size != entry.size_bytes or digest.hexdigest() != entry.sha256:
            raise ValueError("Candidate file differs from its manifest.")
