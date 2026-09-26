"""Immutable raw-artifact storage helpers.

Raw bytes remain the source of truth.  Existing content is never overwritten;
an unexpected payload at an occupied path is stored beside it under its hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import json
import os
import shutil
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"


class RawArtifactConflictError(RuntimeError):
    """Raised only when immutable storage cannot preserve both payloads."""


@dataclass(frozen=True, slots=True)
class StoredRawArtifact:
    path: Path
    sha256: str
    byte_count: int
    stored_at: datetime


def archive_existing_file(
    source_path: Path,
    relative_target: Path,
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
) -> StoredRawArtifact:
    """Copy an existing audit fixture into the canonical immutable raw layout."""

    content = source_path.read_bytes()
    return archive_bytes(content, relative_target, raw_root=raw_root)


def archive_json(
    payload: Any,
    relative_target: Path,
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
) -> StoredRawArtifact:
    """Archive sanitized JSON deterministically; credentials must be removed first."""

    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")
    return archive_bytes(content, relative_target, raw_root=raw_root)


def archive_bytes(
    content: bytes,
    relative_target: Path,
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
) -> StoredRawArtifact:
    """Write immutable bytes atomically and return their checksum metadata."""

    if relative_target.is_absolute() or ".." in relative_target.parts:
        raise ValueError("relative_target must stay inside the raw-data root")
    digest = sha256(content).hexdigest()
    target = raw_root / relative_target
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        existing = target.read_bytes()
        if existing != content:
            target = target.with_name(f"{target.stem}.{digest[:12]}{target.suffix}")
            if target.exists() and target.read_bytes() != content:
                raise RawArtifactConflictError(
                    f"Could not preserve conflicting raw payload at {target}"
                )
        else:
            return _artifact(target, content)

    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    try:
        target.chmod(0o444)
    except OSError:
        # Read-only mode is defense in depth; content hashes remain authoritative.
        pass
    return _artifact(target, content)


def describe_existing_file(path: Path) -> StoredRawArtifact:
    """Hash an already archived file without rewriting or changing its mode."""

    return _artifact(path, path.read_bytes())


def restore_writable_raw_file(path: Path) -> None:
    """Test helper for temporary raw roots; never used by ingestion commands."""

    path.chmod(0o644)


def _artifact(path: Path, content: bytes) -> StoredRawArtifact:
    return StoredRawArtifact(
        path=path,
        sha256=sha256(content).hexdigest(),
        byte_count=len(content),
        stored_at=datetime.now(UTC),
    )
