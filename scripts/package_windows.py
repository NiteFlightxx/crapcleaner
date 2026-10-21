"""Build a windowed Windows application and a distributable ZIP.

Run with the interpreter containing requirements-build.txt:
    python scripts/package_windows.py
    python scripts/package_windows.py --mode onefile
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crapcleaner.constants import APP_URL, VERSION  # noqa: E402


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def verify_gui(executable: Path, environment: dict[str, str], work: Path) -> None:
    """Catch frozen Qt import/plugin failures without touching the user's data."""
    profile = work / "gui-check"
    for folder in ("appdata", "localappdata", "tmp"):
        (profile / folder).mkdir(parents=True, exist_ok=True)
    config = profile / "appdata" / "CrapCleaner" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"config_version": 2, "language": "zh_CN", "offline_mode": True}),
        encoding="utf-8",
    )
    env = dict(environment)
    env.update(
        APPDATA=str(profile / "appdata"),
        LOCALAPPDATA=str(profile / "localappdata"),
        TEMP=str(profile / "tmp"),
        TMP=str(profile / "tmp"),
        QT_QPA_PLATFORM="offscreen",
        PYTHONIOENCODING="utf-8",
    )
    process = subprocess.Popen(
        [str(executable), "--gui"], cwd=profile, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=8)
            raise RuntimeError(f"Packaged GUI exited at startup: {stdout!r} {stderr!r}")
        except subprocess.TimeoutExpired:
            pass
    finally:
        if process.poll() is None:
            process.terminate()
        stdout, stderr = process.communicate(timeout=15)
    (profile / "startup-stderr.log").write_bytes(stderr)
    if any(marker in stderr for marker in (b"Traceback", b"ImportError", b"ModuleNotFoundError")):
        raise RuntimeError(f"Packaged GUI startup failed: {stderr!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("onedir", "onefile"), default="onedir")
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().upper() not in {"AMD64", "X86_64"}:
        parser.error("Build this Windows x64 release on Windows x64.")

    import PyInstaller  # noqa: F401

    output = args.output.resolve()
    name = f"CrapCleaner-{VERSION}-Windows-x64-{args.mode}"
    bundle = output / name
    archive = output / f"{name}.zip"
    checksum = output / f"{name}.sha256"
    if any(path.exists() for path in (bundle, archive, checksum)):
        parser.error("Release output already exists. Use --output with a new directory.")

    work = ROOT / "build" / "windows-release" / args.mode
    temp = work / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update(
        CRAPCLEANER_BUILD_MODE=args.mode,
        TEMP=str(temp),
        TMP=str(temp),
        PYINSTALLER_CONFIG_DIR=str(work / "cache"),
    )
    # Do not resolve Qt dependencies against unrelated tools on the host PATH
    # (for example Poppler's icuuc.dll exports a different ABI from Windows ICU).
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join(
        str(path) for path in (Path(sys.executable).parent, Path(sys.base_prefix),
                              windows / "System32", windows)
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(work / "dist"),
            "--workpath",
            str(work / "work"),
            str(ROOT / "CrapCleaner.spec"),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )

    built = work / "dist" / ("CrapCleaner" if args.mode == "onedir" else "CrapCleaner.exe")
    executable = built / "CrapCleaner.exe" if args.mode == "onedir" else built
    result = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=60,
        check=True,
    )
    if VERSION not in result.stdout:
        raise RuntimeError(f"Packaged executable version check failed: {result.stdout!r}")
    verify_gui(executable, environment, work)

    if args.mode == "onedir":
        shutil.copytree(built, bundle)
    else:
        bundle.mkdir(parents=True)
        shutil.copy2(built, bundle / "CrapCleaner.exe")
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")

    # Retain the licenses shipped in the exact Qt/Python binding wheels used by
    # this build. Include the binding sources offer/location alongside them.
    licenses = bundle / "third-party-licenses"
    shutil.copytree(ROOT / "packaging" / "licenses", licenses, dirs_exist_ok=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("The Python runtime license is missing from this build environment.")
    shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    dependencies = ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6")
    for dependency in dependencies:
        distribution = importlib.metadata.distribution(dependency)
        for entry in distribution.files or ():
            if "licenses" in entry.parts:
                relative = Path(*entry.parts[entry.parts.index("licenses") + 1 :])
                destination = licenses / dependency / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(entry), destination)

    instructions = f"""CrapCleaner {VERSION} - Windows x64

完整解压 ZIP 后，双击 CrapCleaner.exe 启动。无需安装 Python 或运行 BAT。
建议将整个文件夹放在 E 盘或 F 盘；可以为 EXE 创建桌面快捷方式。
{'请保留 _internal 文件夹，它包含必需的 Qt 和 Python 运行库。' if args.mode == 'onedir' else '单文件 EXE 启动时会临时解压运行库到 Windows 临时目录。'}
清理和迁移必须先完成扫描，实际执行前请核对选项和目标路径。
这不是安装程序；删除解压后的程序文件夹即可移除程序。
用户配置、历史记录和日志仍沿用原有的用户数据目录，程序文件位置不会更改它们。
升级文件夹版本时，请关闭程序，再完整替换程序文件夹，避免混用运行库。

当前 fork: {APP_URL}
原作者与 MIT 版权信息见 LICENSE。
Qt / PySide6 / Shiboken 许可证见 third-party-licenses 和运行库中的许可文件。
Qt 与绑定的对应版本源码及构建说明：
https://download.qt.io/official_releases/QtForPython/
https://code.qt.io/cgit/pyside/pyside-setup.git/
https://download.qt.io/archive/qt/
文件夹版本允许替换 _internal 中兼容的 Qt/PySide6 动态库。
该构建未进行代码签名。
"""
    (bundle / "使用说明.txt").write_text(instructions, encoding="utf-8-sig")
    manifest = {
        "application": "CrapCleaner",
        "version": VERSION,
        "mode": args.mode,
        "platform": "Windows-x64",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository": APP_URL,
        "git_commit": git_output("rev-parse", "HEAD"),
        "git_status": git_output("status", "--porcelain"),
        "python": platform.python_version(),
        "dependencies": {
            dep: importlib.metadata.version(dep)
            for dep in (*dependencies, "pyinstaller", "pyinstaller-hooks-contrib")
        },
    }
    (bundle / "build-info.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as packaged:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                packaged.write(path, path.relative_to(output))
    hasher = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Executable: {bundle / 'CrapCleaner.exe'}")
    print(f"Release: {archive}")
    print(f"SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
