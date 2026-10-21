"""Developer storage discovery, migration, and rollback view."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from crapcleaner.config import update_settings
from crapcleaner.core.migration import (
    finalize_migration,
    load_migration_history,
    rollback_migration,
)
from crapcleaner.gui.icons import icon as material_icon
from crapcleaner.gui.views.common import _c, page_header
from crapcleaner.gui.workers import MigrationInspectWorker, MigrationWorker
from crapcleaner.i18n import translate
from crapcleaner.models.migration import MigrationProfile, MigrationState
from crapcleaner.utils.format import format_size


class DeveloperStorageView(QWidget):
    back_requested = Signal()

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self._main = main
        self._theme = "dark"
        self._profiles: list[MigrationProfile] = []
        self._history: list[dict] = []
        self._inspect_worker = None
        self._migration_worker = None
        self._queue: list[MigrationProfile] = []
        self._dry_run = True
        self._pending_profile_ids: set[str] = set()
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(
            page_header(
                "Developer Storage",
                "Move rebuildable development data away from C: and keep future writes on the target drive.",
            ),
            1,
        )
        self.back_button = QPushButton("Back to Cleanup Manager")
        self.back_button.clicked.connect(self.back_requested.emit)
        header.addWidget(self.back_button)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setIcon(material_icon("refresh", "#ffffff"))
        self.refresh_button.setProperty("primary", "true")
        self.refresh_button.clicked.connect(self.refresh_profiles)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        target_card = QFrame()
        target_card.setProperty("card", "true")
        target_layout = QVBoxLayout(target_card)
        target_layout.setContentsMargins(14, 12, 14, 12)
        target_layout.setSpacing(8)
        target_layout.addWidget(QLabel("Migration Targets"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Developer cache root"))
        self.dev_root_edit = QLineEdit()
        self.dev_root_edit.setText(str(self._main._settings.get("migration_dev_root", r"F:\DevCache")))
        self.dev_root_edit.setMinimumWidth(300)
        row.addWidget(self.dev_root_edit, 1)
        dev_browse = QPushButton("Browse...")
        dev_browse.clicked.connect(lambda: self._choose_root(self.dev_root_edit))
        row.addWidget(dev_browse)
        target_layout.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("JetBrains root"))
        self.jb_root_edit = QLineEdit()
        self.jb_root_edit.setText(str(self._main._settings.get("migration_jetbrains_root", r"F:\JetBrains")))
        self.jb_root_edit.setMinimumWidth(300)
        row2.addWidget(self.jb_root_edit, 1)
        jb_browse = QPushButton("Browse...")
        jb_browse.clicked.connect(lambda: self._choose_root(self.jb_root_edit))
        row2.addWidget(jb_browse)
        target_layout.addLayout(row2)
        self.save_targets_button = QPushButton("Save Target Paths")
        self.save_targets_button.clicked.connect(self._save_targets)
        target_layout.addWidget(self.save_targets_button, 0, Qt.AlignmentFlag.AlignRight)
        root.addWidget(target_card)

        action_row = QHBoxLayout()
        self.preview_button = QPushButton("Preview Selected")
        self.preview_button.clicked.connect(lambda: self._start_selected(True))
        self.migrate_button = QPushButton("Migrate Selected")
        self.migrate_button.setProperty("primary", "true")
        self.migrate_button.clicked.connect(lambda: self._start_selected(False))
        self.finalize_button = QPushButton("Finalize Last Backup")
        self.finalize_button.clicked.connect(self._finalize_last)
        self.rollback_button = QPushButton("Rollback Last")
        self.rollback_button.setProperty("danger", "true")
        self.rollback_button.clicked.connect(self._rollback_last)
        action_row.addWidget(self.preview_button)
        action_row.addWidget(self.migrate_button)
        action_row.addStretch(1)
        action_row.addWidget(self.finalize_button)
        action_row.addWidget(self.rollback_button)
        root.addLayout(action_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Select", "Profile", "Status", "Source", "Target", "Size"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        root.addWidget(self.table, 1)

        self.status_label = QLabel("Press Refresh to inspect known developer data.")
        self.status_label.setProperty("subtle", "true")
        root.addWidget(self.status_label)

        history_card = QFrame()
        history_card.setProperty("card", "true")
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(14, 10, 14, 10)
        history_layout.addWidget(QLabel("Migration History"))
        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(120)
        history_layout.addWidget(self.history_list)
        root.addWidget(history_card)

        self.refresh_profiles()

    def _choose_root(self, edit: QLineEdit):
        chosen = QFileDialog.getExistingDirectory(self, "Choose target folder", edit.text())
        if chosen:
            edit.setText(chosen)

    def _save_targets(self):
        dev = os.path.normpath(self.dev_root_edit.text().strip())
        jb = os.path.normpath(self.jb_root_edit.text().strip())
        if not dev or not jb:
            QMessageBox.warning(self, "Invalid target", "Both target paths are required.")
            return
        update_settings(migration_dev_root=dev, migration_jetbrains_root=jb)
        self._main._settings["migration_dev_root"] = dev
        self._main._settings["migration_jetbrains_root"] = jb
        self.status_label.setText("Target paths saved. Refreshing profiles...")
        self.refresh_profiles()

    def refresh_profiles(self):
        if self._inspect_worker is not None and self._inspect_worker.isRunning():
            return
        self.status_label.setText("Inspecting developer data...")
        self.refresh_button.setEnabled(False)
        self._inspect_worker = MigrationInspectWorker(parent=self)
        self._main._register_worker(self._inspect_worker)
        self._inspect_worker.done.connect(self._on_profiles)
        self._inspect_worker.failed.connect(self._on_failed)
        self._inspect_worker.finished.connect(lambda: self.refresh_button.setEnabled(True))
        self._inspect_worker.start()

    def _on_profiles(self, profiles: list[MigrationProfile]):
        self._profiles = profiles
        self._populate()
        self._select_pending_profiles()
        self._history = load_migration_history()
        self._populate_history()
        available = sum(1 for profile in profiles if profile.state == MigrationState.READY)
        linked = sum(1 for profile in profiles if profile.state == MigrationState.LINKED)
        if getattr(self._main, "_language", "en") == "zh_CN":
            self.status_label.setText(f"{available} 个项目可以迁移，{linked} 个已联接。没有修改任何文件。")
        else:
            self.status_label.setText(
                f"{available} profile(s) ready, {linked} already linked. No files were changed."
            )

    def _populate(self):
        self.table.setRowCount(0)
        language = getattr(self._main, "_language", "en")
        for profile in self._profiles:
            row = self.table.rowCount()
            self.table.insertRow(row)
            checkbox = QTableWidgetItem()
            checkbox.setCheckState(Qt.CheckState.Unchecked)
            checkbox.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.table.setItem(row, 0, checkbox)
            self.table.setItem(row, 1, QTableWidgetItem(translate(profile.name, language)))
            status = self._state_label(profile.state, language)
            self.table.setItem(row, 2, QTableWidgetItem(status))
            self.table.setItem(row, 3, QTableWidgetItem(profile.source))
            self.table.setItem(row, 4, QTableWidgetItem(profile.target))
            self.table.setItem(row, 5, QTableWidgetItem(format_size(profile.size)))
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(3, 300)
        self.table.setColumnWidth(4, 300)

    def select_profile_ids(self, profile_ids: list[str]):
        """Preselect profiles passed from the unified cleanup manager."""
        self._pending_profile_ids = set(profile_ids)
        self._select_pending_profiles()

    def _select_pending_profiles(self):
        if not self._pending_profile_ids:
            return
        matched = False
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            profile_id = str(item.data(Qt.ItemDataRole.UserRole))
            if profile_id in self._pending_profile_ids or any(
                profile_id.startswith(prefix) for prefix in self._pending_profile_ids
            ):
                item.setCheckState(Qt.CheckState.Checked)
                matched = True
        if matched:
            self._pending_profile_ids.clear()

    @staticmethod
    def _state_label(state: MigrationState, language: str) -> str:
        labels = {
            MigrationState.MISSING: "Missing",
            MigrationState.READY: "Ready",
            MigrationState.LINKED: "Linked",
            MigrationState.BLOCKED: "Protected",
            MigrationState.FAILED: "Failed",
            MigrationState.MIGRATED: "Migrated",
        }
        return translate(labels.get(state, state.value), language)

    def _populate_history(self):
        self.history_list.clear()
        language = getattr(self._main, "_language", "en")
        for record in reversed(self._history[-20:]):
            if record.get("operation") != "migrate":
                continue
            state = "成功" if record.get("success") else "失败"
            if language == "en":
                state = "Success" if record.get("success") else "Failed"
            self.history_list.addItem(
                f"{state}  {record.get('profile_id', '')}  {record.get('message', '')}"
            )

    def _selected_profiles(self) -> list[MigrationProfile]:
        selected_ids = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected_ids.add(str(item.data(Qt.ItemDataRole.UserRole)))
        return [profile for profile in self._profiles if profile.id in selected_ids]

    def _start_selected(self, dry_run: bool):
        profiles = [p for p in self._selected_profiles() if p.state == MigrationState.READY]
        if not profiles:
            QMessageBox.information(self, "Migration", "Select at least one ready profile.")
            return
        if not dry_run:
            answer = QMessageBox.question(
                self,
                "Confirm Migration",
                "Files will be copied, SHA-256 verified, and the original paths replaced with directory junctions. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._queue = profiles
        self._dry_run = dry_run
        self._run_next()

    def _run_next(self):
        if not self._queue:
            self.status_label.setText("Migration queue finished.")
            self.refresh_profiles()
            return
        profile = self._queue.pop(0)
        language = getattr(self._main, "_language", "en")
        prefix = "正在执行" if language == "zh_CN" else "Running"
        self.status_label.setText(f"{prefix} {profile.name}...")
        self.preview_button.setEnabled(False)
        self.migrate_button.setEnabled(False)
        self._migration_worker = MigrationWorker(profile, dry_run=self._dry_run, parent=self)
        self._main._register_worker(self._migration_worker)
        self._migration_worker.progress.connect(
            lambda stage, current, total, item: self.status_label.setText(
                f"{profile.name}: {('复制' if stage == 'copy' and getattr(self._main, '_language', 'en') == 'zh_CN' else stage)} {current}/{total} {item}"
            )
        )
        self._migration_worker.done.connect(self._on_migration_done)
        self._migration_worker.failed.connect(self._on_failed)
        self._migration_worker.finished.connect(self._enable_actions)
        self._migration_worker.start()

    def _enable_actions(self):
        self.preview_button.setEnabled(True)
        self.migrate_button.setEnabled(True)

    def _on_migration_done(self, result):
        if result.success:
            self.status_label.setText(result.message)
        else:
            if getattr(self._main, "_language", "en") == "zh_CN":
                self.status_label.setText(f"迁移失败：{result.message}")
            else:
                self.status_label.setText(f"Migration failed: {result.message}")
        self._run_next()

    def _on_failed(self, message: str):
        self.status_label.setText(message)
        self._queue.clear()
        self._enable_actions()

    def _last_record(self):
        for record in reversed(self._history):
            if record.get("operation") == "migrate" and record.get("success"):
                return record
        return None

    def _finalize_last(self):
        record = self._last_record()
        if not record:
            QMessageBox.information(self, "Migration", "No successful migration backup was found.")
            return
        answer = QMessageBox.question(
            self,
            "Finalize Migration",
            "Delete the retained rollback backup? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok, message = finalize_migration(record)
        self.status_label.setText(message)
        if ok:
            self.refresh_profiles()

    def _rollback_last(self):
        record = self._last_record()
        if not record:
            QMessageBox.information(self, "Migration", "No successful migration was found.")
            return
        answer = QMessageBox.question(
            self,
            "Rollback Migration",
            "Restore the original path and environment values? The target data will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok, message = rollback_migration(record)
        self.status_label.setText(message)
        if ok:
            self.refresh_profiles()

    def apply_language(self, language: str):
        self.table.setHorizontalHeaderLabels(
            [
                translate("Select", language),
                translate("Profile", language),
                translate("Status", language),
                translate("Source", language),
                translate("Target", language),
                translate("Size", language),
            ]
        )
        self._populate()
        self._populate_history()

    def apply_theme(self, theme: str):
        self._theme = theme
        self.status_label.setStyleSheet(f"color: {_c(theme, 'muted')};")
