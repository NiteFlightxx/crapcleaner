"""Models for developer-cache migration and rollback."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MigrationState(str, Enum):
    MISSING = "missing"
    READY = "ready"
    LINKED = "linked"
    BLOCKED = "blocked"
    FAILED = "failed"
    MIGRATED = "migrated"


@dataclass
class MigrationProfile:
    id: str
    name: str
    source: str
    target: str
    kind: str
    description: str
    env_updates: dict[str, str] = field(default_factory=dict)
    process_names: tuple[str, ...] = ()
    state: MigrationState = MigrationState.MISSING
    size: int = 0
    file_count: int = 0
    skipped_count: int = 0
    source_target: str = ""
    backup_path: str = ""
    reason: str = ""

    @property
    def source_exists(self) -> bool:
        return bool(self.source) and (self.source == self.source_target or os.path.exists(self.source))

    @property
    def available(self) -> bool:
        return self.state in (MigrationState.READY, MigrationState.MIGRATED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "description": self.description,
            "env_updates": dict(self.env_updates),
            "process_names": list(self.process_names),
            "state": self.state.value,
            "size": self.size,
            "file_count": self.file_count,
            "skipped_count": self.skipped_count,
            "source_target": self.source_target,
            "backup_path": self.backup_path,
            "reason": self.reason,
        }


@dataclass
class MigrationResult:
    profile_id: str
    success: bool
    dry_run: bool
    source: str
    target: str
    files_copied: int = 0
    bytes_copied: int = 0
    verified_files: int = 0
    backup_path: str = ""
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    env_changes: dict[str, dict[str, str | None]] = field(default_factory=dict)
    audit_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "success": self.success,
            "dry_run": self.dry_run,
            "source": self.source,
            "target": self.target,
            "files_copied": self.files_copied,
            "bytes_copied": self.bytes_copied,
            "verified_files": self.verified_files,
            "backup_path": self.backup_path,
            "message": self.message,
            "warnings": list(self.warnings),
            "env_changes": self.env_changes,
            "audit_path": self.audit_path,
        }
