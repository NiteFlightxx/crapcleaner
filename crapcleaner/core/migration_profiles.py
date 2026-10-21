"""Known developer-data locations and their future-write configuration."""

from __future__ import annotations

import os
from pathlib import Path

from crapcleaner.models.migration import MigrationProfile


def _settings_targets() -> tuple[str, str]:
    try:
        from crapcleaner.config import load_settings

        settings = load_settings()
    except Exception:
        settings = {}
    dev = str(settings.get("migration_dev_root") or r"F:\DevCache")
    jb = str(settings.get("migration_jetbrains_root") or r"F:\JetBrains")
    return os.path.expandvars(os.path.expanduser(dev)), os.path.expandvars(os.path.expanduser(jb))


def _env(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)


def _add_unique(items: list[MigrationProfile], profile: MigrationProfile) -> None:
    key = os.path.normcase(os.path.normpath(profile.source))
    if profile.source and any(os.path.normcase(os.path.normpath(p.source)) == key for p in items):
        return
    items.append(profile)


def discover_profiles() -> list[MigrationProfile]:
    """Return known profiles without walking their contents."""
    user = _env("USERPROFILE", str(Path.home()))
    local = _env("LOCALAPPDATA", os.path.join(user, "AppData", "Local"))
    appdata = _env("APPDATA", os.path.join(user, "AppData", "Roaming"))
    program_data = _env("PROGRAMDATA", r"C:\ProgramData")
    dev_root, jb_root = _settings_targets()
    items: list[MigrationProfile] = []

    def add(
        profile_id: str,
        name: str,
        source: str,
        target: str,
        kind: str,
        description: str,
        env_updates: dict[str, str] | None = None,
        process_names: tuple[str, ...] = (),
    ) -> None:
        _add_unique(
            items,
            MigrationProfile(
                id=profile_id,
                name=name,
                source=os.path.normpath(source),
                target=os.path.normpath(target),
                kind=kind,
                description=description,
                env_updates=env_updates or {},
                process_names=process_names,
            ),
        )

    add(
        "nuget_packages",
        "NuGet Global Package Cache",
        os.path.join(user, ".nuget", "packages"),
        os.path.join(dev_root, "NuGet", "packages"),
        "cache",
        "Re-downloadable NuGet packages; projects and SDKs are not included.",
        {"NUGET_PACKAGES": os.path.join(dev_root, "NuGet", "packages")},
        ("msbuild.exe", "dotnet.exe", "devenv.exe"),
    )
    add(
        "nuget_http_cache",
        "NuGet HTTP Cache",
        os.path.join(local, "NuGet", "v3-cache"),
        os.path.join(dev_root, "NuGet", "http-cache"),
        "cache",
        "NuGet download metadata and HTTP response cache.",
        {"NUGET_HTTP_CACHE_PATH": os.path.join(dev_root, "NuGet", "http-cache")},
        ("msbuild.exe", "dotnet.exe"),
    )
    npm_source = os.environ.get("NPM_CONFIG_CACHE", os.path.join(local, "npm-cache"))
    add(
        "npm_cache",
        "npm Cache",
        npm_source,
        os.path.join(dev_root, "npm"),
        "cache",
        "npm download cache; project node_modules, source, and lock files are not included.",
        {"NPM_CONFIG_CACHE": os.path.join(dev_root, "npm")},
        ("node.exe", "npm.exe"),
    )
    add(
        "unreal_ddc",
        "Unreal Engine DDC",
        os.path.join(local, "UnrealEngine", "Common", "DerivedDataCache"),
        os.path.join(dev_root, "Unreal", "DDC"),
        "cache",
        "Unreal Engine derived data cache; rebuildable and separate from project assets.",
        {"UE-LocalDataCachePath": os.path.join(dev_root, "Unreal", "DDC")},
        ("UnrealEditor.exe", "UnrealEditor-Win64-Debug.exe"),
    )
    add(
        "unreal_zen",
        "Unreal Engine Zen Data",
        os.path.join(local, "UnrealEngine", "Common", "Zen", "Data"),
        os.path.join(dev_root, "Unreal", "Zen"),
        "cache",
        "Local Unreal Zen data; rebuildable and separate from project source.",
        {"UE-ZenDataPath": os.path.join(dev_root, "Unreal", "Zen")},
        ("UnrealEditor.exe", "ZenServer.exe"),
    )
    add(
        "epic_uba",
        "Epic UnrealBuildAccelerator",
        os.path.join(program_data, "Epic", "UnrealBuildAccelerator"),
        os.path.join(dev_root, "UBA"),
        "cache",
        "Epic UBA build cache; rebuildable.",
        {"UBA_ROOT": os.path.join(dev_root, "UBA")},
        ("UnrealBuildAccelerator.exe", "UnrealEditor.exe", "msbuild.exe"),
    )
    add(
        "horde_uba",
        "Horde UBA Cache",
        r"C:\HordeAgent\Sandbox\Saved\Uba",
        os.path.join(dev_root, "Horde", "UBA"),
        "cache",
        "UBA build cache used by Horde Agent; rebuildable.",
        {"UBA_ROOT": os.path.join(dev_root, "Horde", "UBA")},
        ("HordeAgent.exe", "UnrealBuildAccelerator.exe"),
    )

    jetbrains_roots = [os.path.join(local, "JetBrains"), os.path.join(appdata, "JetBrains")]
    seen_jb: set[str] = set()
    for base in jetbrains_roots:
        try:
            children = list(Path(base).iterdir())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir() or child.name.lower() in seen_jb:
                continue
            if not any(token in child.name.lower() for token in ("rider", "resharper", "clion", "idea", "pycharm")):
                continue
            seen_jb.add(child.name.lower())
            add(
                f"jetbrains_{child.name.lower()}",
                f"JetBrains {child.name}",
                str(child),
                os.path.join(jb_root, child.name),
                "jetbrains",
                "JetBrains IDE cache, indexes, and logs. Close the IDE before migrating.",
                {},
                ("rider64.exe", "idea64.exe", "pycharm64.exe", "clion64.exe"),
            )
    if not any(p.kind == "jetbrains" for p in items):
        add(
            "jetbrains_rider",
            "JetBrains Rider Data",
            os.path.join(local, "JetBrains", "Rider2026.2"),
            os.path.join(jb_root, "Rider2026.2"),
            "jetbrains",
            "JetBrains Rider cache, indexes, and logs. Close Rider before migrating.",
            {},
            ("rider64.exe", "rider.exe"),
        )

    return items
