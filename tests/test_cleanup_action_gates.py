"""Regression coverage for the scan -> select -> action workflow."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from crapcleaner.gui.views.cleanup import CleanupView
from crapcleaner.models.category import CacheTarget, CleanupCategory, SafetyLevel


@pytest.fixture
def view(qt_app):
    main = SimpleNamespace(
        _settings={"scan_cache_ttl": 300}, _language="en",
        start_scan=lambda: None, clean_selected=lambda: None,
    )
    return CleanupView(main)


def categories():
    return [
        CleanupCategory("npm_cache", "npm cache", "", SafetyLevel.SAFE, group="Node.js"),
        CleanupCategory("nuget_cache", "NuGet cache", "", SafetyLevel.SAFE, group=".NET"),
    ]


@pytest.mark.parametrize("selection", ["safe", "all"])
def test_empty_checked_category_does_not_block_measured_data(view, selection):
    filled, empty = categories()
    view.populate([filled, empty])
    if selection == "safe":
        view._select_by_safety(True)
    else:
        view._select_all(True)
    view.set_scanning(True)
    filled.size = 1024
    view.set_scanning(False)
    view.update_sizes()
    view.set_scan_result_valid(True)

    assert view.clean_button.isEnabled()
    assert view.migrate_button.isEnabled()
    assert view.categories_for_action("clean") == [filled]
    assert view.categories_for_action("migrate") == [filled]
    assert empty in view.selected_categories()


def test_rescan_that_empties_a_previously_selected_category(view):
    filled, emptied = categories()
    filled.size = emptied.size = 1024
    view.populate([filled, emptied])
    view.set_scan_result_valid(True)
    view.set_scanning(True)
    emptied.size = 0
    view.set_scanning(False)
    view.update_sizes()
    view.set_scan_result_valid(True)
    assert view.clean_button.isEnabled()
    assert view.migrate_button.isEnabled()
    assert view.categories_for_action("clean") == [filled]


@pytest.mark.parametrize("state", ["unscanned", "scanning", "cancelled", "expired", "cleaning", "migrating", "empty", "unselected"])
def test_safety_gates_still_disable_buttons_and_handlers(view, state):
    filled, empty = categories()
    filled.size = 1024
    view.populate([filled, empty])
    view._select_all(True)
    if state != "unscanned":
        view.set_scan_result_valid(True)
    if state in {"scanning", "cancelled"}:
        view.set_scanning(True)
        if state == "cancelled":
            view.set_scanning(False)
    elif state == "expired":
        view._scan_completed_at -= 301
    elif state == "cleaning":
        view.set_cleaning(True)
    elif state == "migrating":
        view._migration_active = True
    elif state == "empty":
        filled.size = 0
    elif state == "unselected":
        view._select_all(False)
    view._update_summary()
    assert not view.clean_button.isEnabled()
    assert not view.migrate_button.isEnabled()
    assert view.categories_for_action("clean") == []
    assert view.categories_for_action("migrate") == []


def test_timer_locks_an_expired_result_without_changing_selection(view, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("crapcleaner.gui.views.cleanup.time.monotonic", lambda: clock[0])
    filled, empty = categories()
    filled.size = 1024
    view.populate([filled, empty])
    view.set_scan_result_valid(True)
    assert view.clean_button.isEnabled()
    clock[0] += 301
    view._scan_gate_timer.timeout.emit()
    assert not view.clean_button.isEnabled()
    assert not view.migrate_button.isEnabled()


def test_only_non_migratable_data_enables_cleanup_alone(view):
    filled, empty = categories()
    filled.id = "browser_cache"
    filled.size = 1024
    view.populate([filled, empty])
    view._select_all(True)
    view.set_scan_result_valid(True)
    assert view.clean_button.isEnabled()
    assert not view.migrate_button.isEnabled()


def test_migration_confirmation_cannot_bypass_scan_expiry(view, monkeypatch):
    from crapcleaner.gui.workers import MigrationWorker

    filled, empty = categories()
    filled.size = 1024
    view.populate([filled, empty])
    view.set_scan_result_valid(True)
    monkeypatch.setattr(
        "crapcleaner.gui.views.cleanup.discover_profiles",
        lambda: [SimpleNamespace(id="npm_cache")],
    )

    def confirm_after_expiry(*args, **kwargs):
        view._scan_completed_at -= 301
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm_after_expiry)
    with patch.object(MigrationWorker, "start") as start:
        view.migrate_button.click()
        start.assert_not_called()
    assert not view.migrate_button.isEnabled()


def test_cleanup_failure_requires_rescan_but_does_not_leave_a_busy_lock(qt_app, monkeypatch):
    from crapcleaner.gui import app as app_module
    from crapcleaner.gui.views.dashboard import DashboardView

    filled, empty = categories()
    filled.size = 1024
    monkeypatch.setattr(app_module, "get_all_categories", lambda: [filled, empty])
    monkeypatch.setattr(DashboardView, "refresh", lambda self: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    window = app_module.MainWindow()
    view = window.cleanup_view
    view.set_scan_result_valid(True)
    view.set_cleaning(True)
    window._on_clean_failed("test failure")
    assert not view.clean_button.isEnabled()
    assert not view._cleaning
    view.set_scan_result_valid(True)
    assert view.clean_button.isEnabled() and view.migrate_button.isEnabled()
    window.close()


def test_real_scan_completion_and_button_clicks_use_the_same_non_empty_selection(
    qt_app, tmp_path, monkeypatch
):
    from crapcleaner.gui import app as app_module
    from crapcleaner.gui.views.dashboard import DashboardView
    from crapcleaner.gui.workers import CleanWorker, MigrationWorker

    filled, empty = categories()
    data = tmp_path / "data"
    data.mkdir()
    (data / "cache.bin").write_bytes(b"test cache" * 128)
    missing = tmp_path / "missing-cache"
    filled.targets = [CacheTarget(str(data))]
    empty.targets = [CacheTarget(str(missing))]
    monkeypatch.setattr(app_module, "get_all_categories", lambda: [filled, empty])
    monkeypatch.setattr(DashboardView, "refresh", lambda self: None)
    window = app_module.MainWindow()
    view = window.cleanup_view
    view._select_by_safety(True)
    window.start_scan()
    deadline = time.monotonic() + 10
    while not view._scan_valid and time.monotonic() < deadline:
        QTest.qWait(10)
    assert view._scan_valid, "Scan completion never unlocked the fresh result"
    assert filled.size > 0 and empty.size == 0
    assert view.clean_button.isEnabled() and view.migrate_button.isEnabled()
    window._settings.update(confirm_cleanup=False, dry_run_default=True)

    # Exercise real Qt button signals/handlers, but never start a delete/move worker.
    with patch.object(CleanWorker, "start") as start:
        view.clean_button.click()
        start.assert_called_once()
        assert window._workers[-1]._categories == [filled]
    view.set_cleaning(False)
    profile = SimpleNamespace(id="npm_cache", name="npm cache")
    monkeypatch.setattr("crapcleaner.gui.views.cleanup.discover_profiles", lambda: [profile])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    with patch.object(MigrationWorker, "start") as start:
        view.migrate_button.click()
        start.assert_called_once()
        assert window._workers[-1]._profile is profile
    assert (data / "cache.bin").read_bytes() == b"test cache" * 128
    assert not missing.exists()
    window.close()
