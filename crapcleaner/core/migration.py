"""Verified copy, directory-junction, configuration, and rollback operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from crapcleaner.config import config_dir
from crapcleaner.core.migration_profiles import discover_profiles
from crapcleaner.core.protected_paths import explain_protection
from crapcleaner.core.size import compute_dir_size
from crapcleaner.models.migration import MigrationProfile, MigrationResult, MigrationState
from crapcleaner.utils.files import is_link_like, walk_safe
from crapcleaner.utils.platform import is_windows


ProgressCallback = Callable[[str, int, int, str], None] | None
MIGRATION_HISTORY_FILE = "migration-history.jsonl"


def _history_path() -> str:
    return os.path.join(config_dir(), MIGRATION_HISTORY_FILE)


def _normal(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _is_same_or_child(path: str, root: str) -> bool:
    p, r = _normal(path), _normal(root)
    return p == r or p.startswith(r + os.sep)


def junction_target(path: str) -> str:
    if not os.path.lexists(path):
        return ""
    try:
        if is_link_like(os.stat(path, follow_symlinks=False)):
            return os.path.normpath(os.path.realpath(path))
    except OSError:
        return ""
    return ""


def inspect_profile(profile: MigrationProfile, max_files: int = 500_000) -> MigrationProfile:
    """Measure one profile and assign a non-destructive state."""
    profile.source_target = junction_target(profile.source)
    if profile.source_target:
        if not os.path.exists(profile.source_target):
            profile.state = MigrationState.FAILED
            profile.reason = "源路径是悬空目录联接"
            return profile
        profile.state = MigrationState.LINKED
        profile.reason = f"源路径已经是目录联接，目标为 {profile.source_target}"
        size_root = profile.source_target
    elif not os.path.exists(profile.source):
        profile.state = MigrationState.MISSING
        profile.reason = "源路径不存在"
        return profile
    else:
        protection = explain_protection(profile.source)
        if protection:
            profile.state = MigrationState.BLOCKED
            profile.reason = protection
            return profile
        size_root = profile.source

    try:
        profile.size, profile.file_count, profile.skipped_count = compute_dir_size(
            size_root, max_files=max_files
        )
    except (OSError, ValueError) as exc:
        profile.state = MigrationState.BLOCKED
        profile.reason = str(exc)
        return profile
    if profile.state != MigrationState.LINKED:
        profile.state = MigrationState.READY
        profile.reason = "可以预览迁移"
    return profile


def inspect_all_profiles(max_files: int = 500_000) -> list[MigrationProfile]:
    return [inspect_profile(p, max_files=max_files) for p in discover_profiles()]


def _hash_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            block = stream.read(chunk_size)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _iter_files(root: str):
    for directory, _dirs, files in walk_safe(root):
        for name in files:
            source = os.path.join(directory, name)
            try:
                if is_link_like(os.stat(source, follow_symlinks=False)):
                    continue
            except OSError:
                continue
            yield source


def _process_snapshot() -> str:
    if not is_windows():
        return ""
    try:
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.lower()


def _locked_processes(profile: MigrationProfile) -> list[str]:
    snapshot = _process_snapshot()
    if not snapshot:
        return []
    return [name for name in profile.process_names if name.lower() in snapshot]


def _volume_is_usable(target: str) -> tuple[bool, str]:
    if not target:
        return False, "目标路径为空"
    if not is_windows():
        return True, ""
    drive, _ = os.path.splitdrive(os.path.abspath(target))
    if not drive:
        return False, "目标必须位于本地磁盘"
    try:
        import ctypes

        fs = ctypes.create_unicode_buffer(261)
        root = f"{drive}\\"
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), None, 0, None, None, None, fs, 261
        )
        if ok and fs.value.upper() not in {"NTFS", "REFS"}:
            return False, f"目标文件系统为 {fs.value}，不支持目录联接迁移"
    except (AttributeError, OSError):
        pass
    return True, ""


def _write_junction(link: str, target: str) -> None:
    if not is_windows():
        raise RuntimeError("目录联接迁移只支持 Windows")
    parent = os.path.dirname(link)
    if parent:
        os.makedirs(parent, exist_ok=True)
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", link, target],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not junction_target(link):
        message = (completed.stderr or completed.stdout or "创建目录联接失败").strip()
        raise RuntimeError(message)


def _copy_and_verify(
    source: str,
    staging: str,
    progress: ProgressCallback = None,
    stop_event=None,
) -> tuple[int, int, int]:
    files = list(_iter_files(source))
    total = len(files)
    copied = 0
    bytes_copied = 0
    verified = 0
    for index, source_file in enumerate(files, 1):
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("迁移已取消")
        relative = os.path.relpath(source_file, source)
        target_file = os.path.join(staging, relative)
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        shutil.copy2(source_file, target_file)
        source_hash = _hash_file(source_file)
        target_hash = _hash_file(target_file)
        if source_hash != target_hash:
            raise RuntimeError(f"校验失败：{relative}")
        copied += 1
        verified += 1
        try:
            bytes_copied += os.path.getsize(source_file)
        except OSError:
            pass
        if progress is not None:
            progress("copy", index, total, relative)
    return copied, bytes_copied, verified


def _update_environment(
    updates: dict[str, str],
) -> tuple[dict[str, dict[str, str | None]], list[str]]:
    changes: dict[str, dict[str, str | None]] = {}
    warnings: list[str] = []
    if not updates:
        return changes, warnings
    if not is_windows():
        warnings.append("当前平台不支持持久化 Windows 用户环境变量")
        return changes, warnings
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            for name, value in updates.items():
                try:
                    old, _kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    old = None
                winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
                os.environ[name] = value
                changes[name] = {"old": old, "new": value}
    except (OSError, ImportError) as exc:
        warnings.append(f"环境变量未更新：{exc}")
    return changes, warnings


def _restore_environment(changes: dict[str, dict[str, str | None]]) -> list[str]:
    warnings: list[str] = []
    if not is_windows() or not changes:
        return warnings
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            for name, change in changes.items():
                old = change.get("old")
                if old is None:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                    os.environ.pop(name, None)
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, old)
                    os.environ[name] = old
    except (OSError, ImportError) as exc:
        warnings.append(f"环境变量未恢复：{exc}")
    return warnings


def _append_audit(result: MigrationResult, operation: str = "migrate") -> str:
    os.makedirs(config_dir(), exist_ok=True)
    path = _history_path()
    record = {"timestamp": time.time(), "operation": operation, **result.to_dict()}
    temp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with open(temp, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with open(path, "a", encoding="utf-8") as stream, open(temp, encoding="utf-8") as staged:
        shutil.copyfileobj(staged, stream)
    try:
        os.remove(temp)
    except OSError:
        pass
    return path


def load_migration_history() -> list[dict[str, Any]]:
    path = _history_path()
    if not os.path.isfile(path):
        return []
    records: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return records
    return records


def migrate_profile(
    profile: MigrationProfile,
    dry_run: bool = True,
    progress: ProgressCallback = None,
    stop_event=None,
) -> MigrationResult:
    """Copy, hash-verify, link, and configure one profile."""
    result = MigrationResult(
        profile_id=profile.id,
        success=False,
        dry_run=dry_run,
        source=profile.source,
        target=profile.target,
    )
    inspected = inspect_profile(profile)
    if inspected.state == MigrationState.MISSING:
        result.message = "源路径不存在"
        return result
    if inspected.state == MigrationState.BLOCKED:
        result.message = inspected.reason
        return result
    if inspected.state == MigrationState.LINKED:
        if _normal(inspected.source_target) == _normal(profile.target):
            result.success = True
            result.message = "源路径已经正确联接到目标路径"
        else:
            result.message = f"源路径已经联接到其他目标：{inspected.source_target}"
        return result
    if os.path.exists(profile.target):
        result.message = "目标路径已经存在，为避免覆盖未自动合并"
        return result
    if _is_same_or_child(profile.target, profile.source):
        result.message = "目标路径不能位于源路径内部"
        return result
    protection = explain_protection(profile.target)
    if protection:
        result.message = f"目标路径受保护：{protection}"
        return result
    usable, reason = _volume_is_usable(profile.target)
    if not usable:
        result.message = reason
        return result
    locked = _locked_processes(profile)
    if locked:
        result.message = "以下进程仍在运行：" + ", ".join(locked)
        return result
    if dry_run:
        result.success = True
        result.message = "预览通过，实际执行前会复制并校验全部文件"
        return result

    staging = f"{profile.target}.crapcleaner-staging-{uuid.uuid4().hex[:10]}"
    backup = f"{profile.source}.crapcleaner-backup-{uuid.uuid4().hex[:10]}"
    try:
        copied, bytes_copied, verified = _copy_and_verify(
            profile.source, staging, progress=progress, stop_event=stop_event
        )
        result.files_copied = copied
        result.bytes_copied = bytes_copied
        result.verified_files = verified
        os.makedirs(os.path.dirname(profile.target), exist_ok=True)
        os.replace(staging, profile.target)
        os.replace(profile.source, backup)
        _write_junction(profile.source, profile.target)
        probe = os.path.join(profile.source, ".crapcleaner-write-test")
        with open(probe, "w", encoding="utf-8") as stream:
            stream.write("junction-ok")
        if not os.path.isfile(os.path.join(profile.target, ".crapcleaner-write-test")):
            raise RuntimeError("目录联接写入验证失败")
        os.remove(probe)
        changes, warnings = _update_environment(profile.env_updates)
        result.env_changes = changes
        result.warnings.extend(warnings)
        result.backup_path = backup
        result.success = True
        result.message = "迁移完成，原数据已保留为回滚备份"
    except Exception as exc:
        result.message = str(exc)
        result.warnings.append("原始数据未主动删除")
        try:
            if junction_target(profile.source):
                os.rmdir(profile.source)
            if os.path.exists(backup) and not os.path.exists(profile.source):
                os.replace(backup, profile.source)
        except OSError as restore_exc:
            result.warnings.append(f"恢复原路径失败：{restore_exc}")
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
    result.audit_path = _append_audit(result)
    return result


def finalize_migration(record: dict[str, Any]) -> tuple[bool, str]:
    """Remove only the retained backup of a healthy migrated profile."""
    source = str(record.get("source", ""))
    target = str(record.get("target", ""))
    backup = str(record.get("backup_path", ""))
    if not source or not target or not backup:
        return False, "迁移记录不完整"
    if _normal(junction_target(source)) != _normal(target):
        return False, "源路径不是指向目标路径的健康目录联接"
    if not os.path.isdir(backup):
        return True, "回滚备份已经不存在"
    try:
        shutil.rmtree(backup)
    except OSError as exc:
        return False, str(exc)
    return True, "回滚备份已清理"


def rollback_migration(record: dict[str, Any]) -> tuple[bool, str]:
    source = str(record.get("source", ""))
    target = str(record.get("target", ""))
    backup = str(record.get("backup_path", ""))
    if not source or not target or not backup:
        return False, "迁移记录不完整"
    if _normal(junction_target(source)) != _normal(target):
        return False, "源路径不是指向目标路径的健康目录联接"
    if not os.path.isdir(backup):
        return False, "找不到回滚备份，未执行回滚"
    try:
        os.rmdir(source)
        os.replace(backup, source)
        warnings = _restore_environment(record.get("env_changes", {}))
        message = "已恢复原始路径"
        if warnings:
            message += "；" + "；".join(warnings)
        return True, message
    except OSError as exc:
        return False, str(exc)
