"""Safety and action recommendations for the unified cleanup manager."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from crapcleaner.i18n import translate
from crapcleaner.models.category import SafetyLevel


class Recommendation(str, Enum):
    CLEAN = "clean"
    PREFER_MIGRATION = "prefer_migration"
    MIGRATE_ONLY = "migrate_only"
    REVIEW = "review"
    PROTECTED = "protected"
    LINKED = "linked"


@dataclass(frozen=True)
class ActionPolicy:
    recommendation: Recommendation
    can_clean: bool
    can_migrate: bool
    can_link: bool
    reason: str

    def label(self, language: str = "en") -> str:
        labels = {
            Recommendation.CLEAN: "Safe to Clean",
            Recommendation.PREFER_MIGRATION: "Prefer Migration",
            Recommendation.MIGRATE_ONLY: "Migrate Only",
            Recommendation.REVIEW: "Review Required",
            Recommendation.PROTECTED: "Protected",
            Recommendation.LINKED: "Already Linked",
        }
        return translate(labels[self.recommendation], language)


_DEVELOPER_GROUPS = frozenset({"Developer tools", ".NET", "Node.js", "Python"})
_MIGRATABLE_IDS = frozenset(
    {
        "nuget_cache",
        "npm_cache",
        "unreal_ddc",
        "jetbrains_caches",
        "resharper_caches",
    }
)


def migration_profile_ids_for_category(category) -> tuple[str, ...]:
    mapping = {
        "nuget_cache": ("nuget_packages",),
        "npm_cache": ("npm_cache",),
        "unreal_ddc": ("unreal_ddc",),
        "unreal_zen": ("unreal_zen",),
        "jetbrains_caches": ("jetbrains_rider",),
        "resharper_caches": ("jetbrains_rider",),
    }
    return mapping.get(getattr(category, "id", ""), ())


def policy_for_category(category) -> ActionPolicy:
    if category is None:
        return ActionPolicy(Recommendation.REVIEW, False, False, False, "No category selected")

    if getattr(category, "reversible", True) is False:
        return ActionPolicy(
            Recommendation.PROTECTED,
            False,
            False,
            False,
            "This operation cannot be reversed safely.",
        )
    if getattr(category, "safety_level", None) == SafetyLevel.DANGEROUS:
        return ActionPolicy(
            Recommendation.PROTECTED,
            False,
            False,
            False,
            "This category is protected from automatic file operations.",
        )

    group = getattr(category, "group", "")
    category_id = getattr(category, "id", "")
    if category_id in _MIGRATABLE_IDS or group in _DEVELOPER_GROUPS:
        if migration_profile_ids_for_category(category):
            return ActionPolicy(
                Recommendation.PREFER_MIGRATION,
                True,
                True,
                True,
                "It can be rebuilt, but moving it avoids repeated downloads or re-indexing.",
            )
        return ActionPolicy(
            Recommendation.CLEAN,
            True,
            False,
            False,
            "It is a rebuildable developer cache; no safe migration profile is registered yet.",
        )

    if group == "AI":
        return ActionPolicy(
            Recommendation.REVIEW,
            False,
            False,
            False,
            "AI data may include valuable model weights and is read-only by default.",
        )
    if group == "System":
        return ActionPolicy(
            Recommendation.PROTECTED,
            False,
            False,
            False,
            "System data needs a vendor-supported operation.",
        )
    if getattr(category, "safety_level", None) in (SafetyLevel.SAFE, SafetyLevel.LOW_RISK):
        return ActionPolicy(
            Recommendation.CLEAN,
            True,
            False,
            False,
            "The data is rebuildable and the existing cleanup action is the safest operation.",
        )
    return ActionPolicy(
        Recommendation.REVIEW,
        False,
        False,
        False,
        "Review the contents before choosing an operation.",
    )
