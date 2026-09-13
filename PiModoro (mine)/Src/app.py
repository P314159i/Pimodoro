from __future__ import annotations

import calendar
import csv
import json
import math
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QDate, QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication, QCalendarWidget, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QDoubleSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from pimodoro_db import Database

APP_NAME = "PiModoro"
DB_FILE = Path.home() / ".pimodoro.db"
MISC_DIR = Path(__file__).resolve().parent.parent / "misc"
EMOJI_FONT_FILE = MISC_DIR / "NotoColorEmoji-Regular.ttf"

DEFAULT_THEME = {
    "background": "#023d2a", "panel": "#045c3d", "accent": "#05774a",
    "hover": "#07935c", "text": "#e8fff5", "muted": "#b1d8c7",
    "field": "#032f22", "note_paper": "#fffdf5", "note_text": "#1f2937",
    "P1": "#e5484d", "P2": "#f59e0b", "P3": "#3b82f6", "P4": "#94a3b8",
    "opacity": 0.94,
}
PRIORITIES = {"P1": "P1 Critical", "P2": "P2 High", "P3": "P3 Medium", "P4": "P4 Low"}
TRACKING = {"manual": "Manual time", "pomodoro": "Pomodoro progress", "both": "Both"}


def format_duration(seconds: int, include_seconds: bool = False) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if include_seconds else f"{hours}h {minutes:02d}m"


def parse_date(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    return date.fromisoformat(text).isoformat()


def contrast_text(hex_color: str) -> str:
    try:
        color = QColor(hex_color)
        luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
        return "#111111" if luminance > 165 else "#ffffff"
    except Exception:
        return "#ffffff"


def confirm(parent: QWidget, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if widget:
            widget.deleteLater()
        elif child:
            clear_layout(child)


def register_fonts() -> None:
    for filename in ("Mighty-X34Z2.ttf", "Head.ttf", "Flighty.ttf", "NotoColorEmoji-Regular.ttf"):
        path = MISC_DIR / filename
        if path.exists():
            font_id = QFontDatabase.addApplicationFont(str(path))
            if filename == "NotoColorEmoji-Regular.ttf" and font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families and hasattr(QFontDatabase, "setApplicationEmojiFontFamilies"):
                    QFontDatabase.setApplicationEmojiFontFamilies(families)


class TaskDialog(QDialog):
    def __init__(self, parent: QWidget, db: Database, task: Mapping[str, Any] | None = None,
                 folder_id: int | None = None, deadline: str | None = None, timeless: bool = False):
        super().__init__(parent)
        self.db, self.task, self.timeless = db, dict(task or {}), timeless
        self.setWindowTitle("Edit task" if task else "Add task")
        self.resize(680, 720)
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        basics = QWidget(); form = QFormLayout(basics)
        self.title_edit = QLineEdit(str(self.task.get("title", "")))
        self.priority = QComboBox(); self.priority.addItems(PRIORITIES)
        self.priority.setCurrentText(str(self.task.get("priority", "P4")))
        self.tracking = QComboBox(); self.tracking.addItems(TRACKING)
        self.tracking.setCurrentText(str(self.task.get("tracking_mode", "both")))
        self.work = QSpinBox(); self.work.setRange(1, 720); self.work.setValue(int(self.task.get("task_work_minutes", 25)))
        self.rest = QSpinBox(); self.rest.setRange(1, 720); self.rest.setValue(int(self.task.get("task_break_minutes", 5)))
        self.date_edit = QLineEdit(str(self.task.get("deadline") or deadline or ""))
        self.date_edit.setPlaceholderText("YYYY-MM-DD or blank")
        self.folders = db.get_project_folders()
        self.folder = QComboBox(); self.folder.addItem("No folder", None)
        for item in self.folders: self.folder.addItem(str(item["name"]), int(item["id"]))
        selected_folder = self.task.get("folder_id") or folder_id
        index = self.folder.findData(int(selected_folder)) if selected_folder else 0
        self.folder.setCurrentIndex(max(0, index))
        self.notes = QTextEdit(str(self.task.get("notes", ""))); self.notes.setObjectName("noteEditor")
        self.subtasks = QTextEdit()
        if task:
            self.subtasks.setPlainText("\n".join(str(x["text"]) for x in db.get_subtasks(int(task["id"]))))
        form.addRow("Task", self.title_edit); form.addRow("Priority", self.priority)
        form.addRow("Tracking", self.tracking); form.addRow("Work minutes", self.work)
        form.addRow("Break minutes", self.rest); form.addRow("Date", self.date_edit)
        form.addRow("Project folder", self.folder); form.addRow("Notes", self.notes)
        form.addRow("Subtasks, one per line", self.subtasks)
        tabs.addTab(basics, "Task")

        recurring = QWidget(); rform = QFormLayout(recurring)
        self.recur = QCheckBox("Recurring task"); self.recur.setChecked(bool(self.task.get("recurrence_enabled")))
        self.recur_kind = QComboBox(); self.recur_kind.addItems(["days", "weeks", "months"])
        self.recur_kind.setCurrentText(str(self.task.get("recurrence_kind", "days")))
        self.recur_interval = QSpinBox(); self.recur_interval.setRange(1, 999); self.recur_interval.setValue(int(self.task.get("recurrence_interval", 1)))
        self.recur_start = QLineEdit(str(self.task.get("recurrence_start") or date.today().isoformat()))
        self.recur_end = QLineEdit(str(self.task.get("recurrence_end") or ""))
        self.recur_max = QSpinBox(); self.recur_max.setRange(0, 100000); self.recur_max.setValue(int(self.task.get("recurrence_max") or 0))
        self.weekdays = QLineEdit(str(self.task.get("recurrence_weekdays") or "")); self.weekdays.setPlaceholderText("0,1,2 (Monday=0)")
        self.exceptions = QTextEdit()
        if task:
            self.exceptions.setPlainText(", ".join(
                x["start_date"] if x["start_date"] == x["end_date"] else f"{x['start_date']}..{x['end_date']}"
                for x in db.get_exceptions(int(task["id"]))
            ))
        for label, widget in (("", self.recur), ("Every", self.recur_interval), ("Unit", self.recur_kind),
                              ("Weekdays", self.weekdays), ("Start", self.recur_start), ("End", self.recur_end),
                              ("Maximum occurrences (0 = none)", self.recur_max), ("Exceptions", self.exceptions)):
            rform.addRow(label, widget)
        tabs.addTab(recurring, "Recurrence")
        if timeless:
            self.date_edit.clear(); self.date_edit.setEnabled(False); self.recur.setChecked(False); tabs.setTabEnabled(1, False)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.title_edit.setFocus(); self.title_edit.selectAll()
        self.result_data: dict[str, Any] | None = None

    def validate(self) -> None:
        title = " ".join(self.title_edit.text().split())
        if not title:
            QMessageBox.warning(self, APP_NAME, "Task title cannot be empty."); return
        try:
            deadline = None if self.timeless else parse_date(self.date_edit.text())
            enabled = False if self.timeless else self.recur.isChecked()
            start = parse_date(self.recur_start.text()) if enabled else None
            end = parse_date(self.recur_end.text()) if enabled else None
            ranges = []
            for raw in self.exceptions.toPlainText().replace("\n", ",").split(","):
                raw = raw.strip()
                if not raw: continue
                parts = [x.strip() for x in raw.split("..", 1)]
                a = parse_date(parts[0]); b = parse_date(parts[-1])
                if a and b: ranges.append(tuple(sorted((a, b))))
        except ValueError:
            QMessageBox.warning(self, APP_NAME, "Use dates in YYYY-MM-DD format."); return
        self.result_data = {
            "values": {"title": title, "notes": self.notes.toPlainText(), "priority": self.priority.currentText(),
                "tracking_mode": self.tracking.currentText(), "task_work_minutes": self.work.value(),
                "task_break_minutes": self.rest.value(), "deadline": deadline, "folder_id": self.folder.currentData(),
                "recurrence_enabled": enabled, "recurrence_kind": self.recur_kind.currentText(),
                "recurrence_interval": self.recur_interval.value(), "recurrence_weekdays": self.weekdays.text(),
                "recurrence_start": start, "recurrence_end": end,
                "recurrence_max": self.recur_max.value() or None},
            "subtasks": [x.strip() for x in self.subtasks.toPlainText().splitlines() if x.strip()], "exceptions": ranges,
        }
        self.accept()


class FolderDialog(QDialog):
    def __init__(self, parent: QWidget, folder: Mapping[str, Any] | None = None):
        super().__init__(parent); self.setWindowTitle("Edit folder" if folder else "New folder")
        self.name = QLineEdit(str(folder.get("name", "")) if folder else ""); self.color = str(folder.get("color", "#526d82")) if folder else "#526d82"
        layout = QFormLayout(self); layout.addRow("Folder name", self.name)
        self.color_button = QPushButton(self.color); self.color_button.clicked.connect(self.pick_color); layout.addRow("Colour", self.color_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.check); buttons.rejected.connect(self.reject); layout.addRow(buttons)
        self.name.setFocus(); self.name.selectAll()

    def pick_color(self):
        selected = QColorDialog.getColor(QColor(self.color), self)
        if selected.isValid(): self.color = selected.name(); self.color_button.setText(self.color)

    def check(self):
        if self.name.text().strip(): self.accept()


class TaskCard(QFrame):
    edit_requested = Signal(int); delete_requested = Signal(int); toggle_requested = Signal(int)
    date_requested = Signal(int); notes_requested = Signal(int); selected = Signal(int)

    def __init__(self, task: Mapping[str, Any], color: str, folder_mode: bool = False):
        super().__init__(); self.task_id = int(task["id"]); self.start_pos = None
        self.setObjectName("taskCard"); self.setStyleSheet(f"QFrame#taskCard {{background:{color}; border-radius:7px;}}")
        layout = QVBoxLayout(self); layout.setContentsMargins(7, 5, 7, 5); layout.setSpacing(2)
        top = QHBoxLayout(); done = bool(task.get("display_done") or task.get("status") == "completed")
        check = QPushButton("●" if done else "○"); check.setFixedWidth(28); check.clicked.connect(lambda: self.toggle_requested.emit(self.task_id))
        title = QLabel(" ".join(str(task["title"]).split())); title.setWordWrap(True)
        font = title.font(); font.setBold(True); font.setStrikeOut(done); title.setFont(font)
        top.addWidget(check); top.addWidget(title, 1); layout.addLayout(top)
        actions = QHBoxLayout()
        if folder_mode:
            date_button = QPushButton("Date"); date_button.clicked.connect(lambda: self.date_requested.emit(self.task_id)); actions.addWidget(date_button)
        else:
            notes = QPushButton("Notes"); notes.clicked.connect(lambda: self.notes_requested.emit(self.task_id)); actions.addWidget(notes)
        edit = QPushButton("Edit"); edit.clicked.connect(lambda: self.edit_requested.emit(self.task_id)); actions.addWidget(edit)
        delete = QPushButton("Delete"); delete.clicked.connect(lambda: self.delete_requested.emit(self.task_id)); actions.addWidget(delete)
        actions.addStretch(); layout.addLayout(actions)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton: self.start_pos = event.position().toPoint(); self.selected.emit(self.task_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.start_pos is not None and (event.position().toPoint() - self.start_pos).manhattanLength() >= QApplication.startDragDistance():
            drag = QDrag(self); mime = QMimeData(); mime.setText(str(self.task_id)); drag.setMimeData(mime); drag.exec(Qt.DropAction.MoveAction)
        super().mouseMoveEvent(event)


class PriorityLane(QFrame):
    dropped = Signal(int, str)
    def __init__(self, priority: str):
        super().__init__(); self.priority = priority; self.setAcceptDrops(True); self.layout_box = QVBoxLayout(self); self.layout_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        heading = QLabel(PRIORITIES[priority]); font = heading.font(); font.setBold(True); heading.setFont(font); self.layout_box.addWidget(heading)
    def dragEnterEvent(self, event):
        if event.mimeData().text().isdigit(): event.acceptProposedAction()
    def dropEvent(self, event):
        self.dropped.emit(int(event.mimeData().text()), self.priority); event.acceptProposedAction()


class PiModoro(QMainWindow):
    def __init__(self):
        super().__init__(); self.db = Database(DB_FILE); self.db.archive_completed_before(date.today())
        self.theme = dict(DEFAULT_THEME); saved = self.db.get_setting("theme", {})
        if isinstance(saved, dict): self.theme.update({k: v for k, v in saved.items() if k in self.theme})
        self.timer_mode = "work"; self.timer_running = False; self.timer_remaining = int(self.db.get_setting("work_minutes", 25)) * 60
        self.timer_task_id = None; self.timer_started = None; self.clock_started = None; self.current_folder_id = None
        self.setWindowTitle(APP_NAME); self.resize(1380, 860); self.setMinimumSize(1080, 680); self.setWindowOpacity(float(self.theme["opacity"]))
        icon = MISC_DIR / "pomo.png"
        if icon.exists(): self.setWindowIcon(QIcon(str(icon)))
        self.build_ui(); self.apply_theme(); self.refresh_all()
        self.tick = QTimer(self); self.tick.timeout.connect(self.every_second); self.tick.start(1000)

    def build_ui(self):
        central = QWidget(); self.setCentralWidget(central); outer = QHBoxLayout(central); outer.setContentsMargins(0, 0, 0, 0)
        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(190); side = QVBoxLayout(sidebar)
        name = QLabel("PiModoro"); name.setStyleSheet("font-size:22px;font-weight:bold"); side.addWidget(name)
        self.pages = QStackedWidget(); self.nav = {}
        for key, title in (("today", "Today"), ("projects", "Project folders"), ("calendar", "Calendar"), ("history", "History"), ("graveyard", "Graveyard"), ("settings", "Settings")):
            button = QPushButton(title); button.clicked.connect(lambda _=False, k=key: self.show_page(k)); side.addWidget(button); self.nav[key] = button
        side.addStretch(); self.clock_button = QPushButton("Clock in"); self.clock_button.clicked.connect(self.toggle_clock); side.addWidget(self.clock_button)
        self.clock_total = QLabel("Today 0h 00m"); side.addWidget(self.clock_total); outer.addWidget(sidebar)
        content = QVBoxLayout(); header = QHBoxLayout(); self.header_date = QLabel(); self.header_time = QLabel(); self.header_total = QLabel()
        header.addWidget(self.header_date); header.addStretch(); header.addWidget(self.header_total); header.addWidget(self.header_time); content.addLayout(header); content.addWidget(self.pages, 1); outer.addLayout(content, 1)
        self.page_keys = []
        for key, builder in (("today", self.build_today), ("projects", self.build_projects), ("calendar", self.build_calendar), ("history", self.build_history), ("graveyard", self.build_graveyard), ("settings", self.build_settings)):
            page = QWidget(); builder(page); self.pages.addWidget(page); self.page_keys.append(key)

    def apply_theme(self):
        t = self.theme
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{background:{t['background']}; color:{t['text']};}}
            QFrame#sidebar {{background:{t['panel']};}}
            QPushButton {{background:{t['accent']}; color:{t['text']}; border:0; border-radius:5px; padding:6px 9px;}}
            QPushButton:hover {{background:{t['hover']};}}
            QLineEdit,QTextEdit,QComboBox,QSpinBox,QTableWidget,QListWidget,QCalendarWidget {{background:{t['field']}; color:{t['text']}; border:1px solid {t['accent']}; padding:4px;}}
            QTextEdit#noteEditor {{background:{t['note_paper']}; color:{t['note_text']};}}
            QHeaderView::section {{background:{t['panel']}; color:{t['text']}; padding:5px; border:0;}}
        """)

    def show_page(self, key):
        self.pages.setCurrentIndex(self.page_keys.index(key)); self.refresh_all()

    def build_today(self, page):
        root = QVBoxLayout(page); timer = QHBoxLayout(); self.work_total = QLabel(); self.timer_label = QLabel("25:00"); self.timer_label.setStyleSheet("font-size:52px;font-weight:bold"); self.break_total = QLabel()
        timer.addWidget(self.work_total); timer.addStretch(); timer.addWidget(self.timer_label); timer.addStretch(); timer.addWidget(self.break_total); root.addLayout(timer)
        controls = QHBoxLayout()
        for text, fn in (("Work", lambda: self.start_timer("work")), ("Break", lambda: self.start_timer("break")), ("Reset", self.reset_timer)):
            b = QPushButton(text); b.clicked.connect(fn); controls.addWidget(b)
        self.timer_task = QLabel("No task selected"); controls.addWidget(self.timer_task); controls.addStretch(); root.addLayout(controls)
        add = QHBoxLayout(); self.quick = QLineEdit(); self.quick.setPlaceholderText("Add a task for today"); self.quick.returnPressed.connect(self.add_today_task)
        button = QPushButton("Add task"); button.clicked.connect(self.add_today_task); add.addWidget(self.quick); add.addWidget(button); root.addLayout(add)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); holder = QWidget(); self.today_grid = QHBoxLayout(holder); self.today_grid.setAlignment(Qt.AlignmentFlag.AlignTop); scroll.setWidget(holder); root.addWidget(scroll, 1)
        bottom = QHBoxLayout(); archive = QPushButton("Archive selected"); archive.clicked.connect(self.archive_selected); bottom.addWidget(archive); bottom.addStretch(); root.addLayout(bottom)
        self.selected_task_id = None

    def make_board(self, layout, tasks, folder_mode=False):
        clear_layout(layout)
        for priority in PRIORITIES:
            lane = PriorityLane(priority); lane.dropped.connect(self.change_priority); layout.addWidget(lane, 1)
            subset = [x for x in tasks if str(x.get("priority", "P4")) == priority]
            for task in subset:
                card = TaskCard(task, self.theme[priority], folder_mode)
                card.edit_requested.connect(self.edit_task); card.delete_requested.connect(self.delete_task)
                card.toggle_requested.connect(self.toggle_task); card.selected.connect(self.select_task)
                card.notes_requested.connect(self.edit_notes); card.date_requested.connect(self.date_task)
                lane.layout_box.addWidget(card)
            lane.layout_box.addStretch()

    def refresh_today(self): self.make_board(self.today_grid, self.db.get_active_tasks(date.today()))

    def add_today_task(self):
        dialog = TaskDialog(self, self.db, deadline=date.today().isoformat())
        if self.quick.text().strip(): dialog.title_edit.setText(self.quick.text().strip())
        if dialog.exec() and dialog.result_data:
            task_id = self.db.create_task(dialog.result_data["values"]); self.db.replace_subtasks(task_id, dialog.result_data["subtasks"]); self.db.set_exceptions(task_id, dialog.result_data["exceptions"]); self.quick.clear(); self.refresh_all()

    def edit_task(self, task_id):
        task = self.db.get_task(task_id)
        if not task: return
        dialog = TaskDialog(self, self.db, task)
        if dialog.exec() and dialog.result_data:
            self.db.update_task(task_id, dialog.result_data["values"]); self.db.replace_subtasks(task_id, dialog.result_data["subtasks"]); self.db.set_exceptions(task_id, dialog.result_data["exceptions"]); self.refresh_all()

    def select_task(self, task_id): self.selected_task_id = task_id; self.timer_task_id = task_id; self.refresh_timer_labels()

    def toggle_task(self, task_id):
        task = self.db.get_task(task_id)
        if task and task.get("status") == "completed": self.db.reopen_task(task_id, date.today())
        else: self.db.complete_task(task_id, date.today())
        self.refresh_all()

    def delete_task(self, task_id):
        task = self.db.get_task(task_id)
        if task and confirm(self, "Delete permanently", f"Permanently delete '{task['title']}'? This cannot be undone."):
            self.db.delete_task(task_id); self.refresh_all()

    def edit_notes(self, task_id):
        task = self.db.get_task(task_id)
        if not task: return
        dialog = QDialog(self); dialog.setWindowTitle("Notes and subtasks"); layout = QVBoxLayout(dialog)
        notes = QTextEdit(str(task.get("notes", ""))); subtasks = QTextEdit("\n".join(x["text"] for x in self.db.get_subtasks(task_id)))
        layout.addWidget(QLabel("Notes")); layout.addWidget(notes); layout.addWidget(QLabel("Subtasks, one per line")); layout.addWidget(subtasks)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec(): self.db.set_task_notes(task_id, notes.toPlainText()); self.db.replace_subtasks(task_id, subtasks.toPlainText().splitlines()); self.refresh_all()

    def change_priority(self, task_id, priority): self.db.update_task(task_id, {"priority": priority}); self.refresh_all()
    def archive_selected(self):
        if self.selected_task_id and confirm(self, "Archive task", "Archive the selected task?"): self.db.archive_task(self.selected_task_id); self.refresh_all()

    def build_projects(self, page):
        root = QVBoxLayout(page); top = QHBoxLayout(); self.folder_title = QLabel("Project folders"); top.addWidget(self.folder_title); top.addStretch()
        for text, fn in (("New folder", self.new_folder), ("Edit folder", self.edit_folder), ("Delete folder", self.delete_folder), ("Add task", self.add_folder_task)):
            b = QPushButton(text); b.clicked.connect(fn); top.addWidget(b)
        root.addLayout(top); body = QHBoxLayout(); self.folder_list = QListWidget(); self.folder_list.currentItemChanged.connect(self.choose_folder); body.addWidget(self.folder_list, 1)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); holder = QWidget(); self.folder_board = QHBoxLayout(holder); self.folder_board.setAlignment(Qt.AlignmentFlag.AlignTop); scroll.setWidget(holder); body.addWidget(scroll, 4); root.addLayout(body, 1)

    def refresh_projects(self):
        selected = self.current_folder_id; self.folder_list.blockSignals(True); self.folder_list.clear()
        for folder in self.db.get_project_folders():
            item = QListWidgetItem(str(folder["name"])); item.setData(Qt.ItemDataRole.UserRole, int(folder["id"])); self.folder_list.addItem(item)
            if int(folder["id"]) == selected: self.folder_list.setCurrentItem(item)
        self.folder_list.blockSignals(False)
        tasks = self.db.tasks_for_folder(selected) if selected else []
        self.make_board(self.folder_board, tasks, True)

    def choose_folder(self, current, _previous): self.current_folder_id = int(current.data(Qt.ItemDataRole.UserRole)) if current else None; self.refresh_projects()
    def new_folder(self):
        dialog = FolderDialog(self)
        if dialog.exec(): self.current_folder_id = self.db.create_project_folder(dialog.name.text(), dialog.color); self.refresh_all()
    def edit_folder(self):
        folder = self.db.get_project_folder(self.current_folder_id) if self.current_folder_id else None
        if not folder: return
        dialog = FolderDialog(self, folder)
        if dialog.exec(): self.db.update_project_folder(self.current_folder_id, dialog.name.text(), dialog.color); self.refresh_all()
    def delete_folder(self):
        folder = self.db.get_project_folder(self.current_folder_id) if self.current_folder_id else None
        if folder and confirm(self, "Delete folder", f"Delete '{folder['name']}'? Tasks will be kept without a folder."):
            self.db.delete_project_folder(self.current_folder_id); self.current_folder_id = None; self.refresh_all()
    def add_folder_task(self):
        if not self.current_folder_id: return
        dialog = TaskDialog(self, self.db, folder_id=self.current_folder_id, timeless=True)
        if dialog.exec() and dialog.result_data:
            values = dict(dialog.result_data["values"]); values.update({"folder_id": self.current_folder_id, "deadline": None, "recurrence_enabled": False, "recurrence_start": None})
            task_id = self.db.create_task(values); self.db.replace_subtasks(task_id, dialog.result_data["subtasks"]); self.refresh_all()
    def date_task(self, task_id):
        task = self.db.get_task(task_id)
        value, ok = QInputDialog.getText(self, "Task date", "Date (YYYY-MM-DD, blank = unscheduled):", text=str(task.get("deadline") or ""))
        if ok:
            try: self.db.update_task(task_id, {"deadline": parse_date(value), "recurrence_enabled": False}); self.refresh_all()
            except ValueError: QMessageBox.warning(self, APP_NAME, "Use YYYY-MM-DD format.")

    def build_calendar(self, page):
        root = QHBoxLayout(page); self.calendar = QCalendarWidget(); self.calendar.selectionChanged.connect(self.refresh_calendar_detail); root.addWidget(self.calendar, 2)
        right = QVBoxLayout(); self.calendar_label = QLabel(); self.calendar_tasks = QListWidget(); right.addWidget(self.calendar_label); right.addWidget(self.calendar_tasks, 1)
        add = QPushButton("Add task on this date"); add.clicked.connect(self.add_calendar_task); right.addWidget(add); root.addLayout(right, 1)
    def refresh_calendar_detail(self):
        selected = self.calendar.selectedDate().toPython(); self.calendar_label.setText(selected.strftime("%A, %d %B %Y")); self.calendar_tasks.clear()
        for task in self.db.calendar_tasks_for_range(selected, selected).get(selected.isoformat(), []): self.calendar_tasks.addItem(f"{task['priority']}  {task['title']}")
    def add_calendar_task(self):
        selected = self.calendar.selectedDate().toPython(); dialog = TaskDialog(self, self.db, deadline=selected.isoformat())
        if dialog.exec() and dialog.result_data:
            task_id = self.db.create_task(dialog.result_data["values"]); self.db.replace_subtasks(task_id, dialog.result_data["subtasks"]); self.db.set_exceptions(task_id, dialog.result_data["exceptions"]); self.refresh_all()

    def build_history(self, page):
        root = QVBoxLayout(page); filters = QHBoxLayout(); self.history_search = QLineEdit(); self.history_search.setPlaceholderText("Search history"); self.history_search.textChanged.connect(self.refresh_history)
        self.history_status = QComboBox(); self.history_status.addItems(["all", "active", "completed", "archived"]); self.history_status.currentTextChanged.connect(self.refresh_history)
        filters.addWidget(self.history_search); filters.addWidget(self.history_status); root.addLayout(filters)
        self.history = QTableWidget(0, 6); self.history.setHorizontalHeaderLabels(["Title", "Status", "Priority", "Date", "Folder", "Updated"]); self.history.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); root.addWidget(self.history)
        actions = QHBoxLayout()
        for text, fn in (("Edit", self.edit_history), ("Restore", self.restore_history), ("Archive", self.archive_history)):
            b = QPushButton(text); b.clicked.connect(fn); actions.addWidget(b)
        actions.addStretch(); root.addLayout(actions)
    def history_id(self):
        row = self.history.currentRow(); return int(self.history.item(row, 0).data(Qt.ItemDataRole.UserRole)) if row >= 0 else None
    def refresh_history(self):
        records = self.db.search_history(search=self.history_search.text(), status=self.history_status.currentText()); self.history.setRowCount(len(records))
        for row, item in enumerate(records):
            folder = self.db.get_project_folder(int(item["folder_id"])) if item.get("folder_id") else None
            values = [item["title"], item["status"], item["priority"], item.get("occurrence_date") or item.get("deadline") or "", folder["name"] if folder else "", item.get("updated_at") or ""]
            for col, value in enumerate(values): self.history.setItem(row, col, QTableWidgetItem(str(value)))
            self.history.item(row, 0).setData(Qt.ItemDataRole.UserRole, int(item["id"]))
        self.history.resizeColumnsToContents()
    def edit_history(self):
        task_id = self.history_id()
        if task_id: self.edit_task(task_id)
    def restore_history(self):
        task_id = self.history_id()
        if task_id: self.db.restore_task(task_id); self.refresh_all()
    def archive_history(self):
        task_id = self.history_id()
        if task_id: self.db.archive_task(task_id); self.refresh_all()

    def build_graveyard(self, page):
        root = QVBoxLayout(page); root.addWidget(QLabel("Graveyard notebook")); self.graveyard = QTextEdit(); self.graveyard.setObjectName("noteEditor"); self.graveyard.setAcceptRichText(False); self.graveyard.setPlainText(str(self.db.get_setting("graveyard_notes", ""))); root.addWidget(self.graveyard, 1)
        save = QPushButton("Save"); save.clicked.connect(self.save_graveyard); root.addWidget(save)
    def save_graveyard(self): self.db.set_setting("graveyard_notes", self.graveyard.toPlainText())

    def build_settings(self, page):
        root = QVBoxLayout(page); form = QFormLayout(); self.work_setting = QSpinBox(); self.work_setting.setRange(1, 720); self.work_setting.setValue(int(self.db.get_setting("work_minutes", 25)))
        self.break_setting = QSpinBox(); self.break_setting.setRange(1, 720); self.break_setting.setValue(int(self.db.get_setting("rest_minutes", 5)))
        self.lock_setting = QCheckBox(); self.lock_setting.setChecked(bool(self.db.get_setting("lock_enabled", False))); form.addRow("Default work minutes", self.work_setting); form.addRow("Default break minutes", self.break_setting); form.addRow("Lock screen after work", self.lock_setting); root.addLayout(form)
        self.opacity_setting = QDoubleSpinBox(); self.opacity_setting.setRange(0.20, 1.00); self.opacity_setting.setSingleStep(0.05); self.opacity_setting.setDecimals(2); self.opacity_setting.setValue(float(self.theme["opacity"])); form.addRow("Window opacity", self.opacity_setting)
        root.addWidget(QLabel("Colours"))
        color_grid = QGridLayout(); self.color_buttons = {}
        color_labels = {
            "background": "Background", "panel": "Side panel", "accent": "Buttons",
            "hover": "Button hover", "text": "Main text", "muted": "Muted text",
            "field": "Input fields", "note_paper": "Notes paper", "note_text": "Notes text",
            "P1": "P1 Critical", "P2": "P2 High", "P3": "P3 Medium", "P4": "P4 Low",
        }
        for index, (key, label) in enumerate(color_labels.items()):
            row, column = divmod(index, 2)
            box = QHBoxLayout(); box.addWidget(QLabel(label))
            button = QPushButton(self.theme[key]); button.clicked.connect(lambda _=False, k=key: self.choose_theme_color(k)); self.color_buttons[key] = button; self.update_color_button(key); box.addWidget(button)
            color_grid.addLayout(box, row, column)
        root.addLayout(color_grid)
        settings_actions = QHBoxLayout(); save = QPushButton("Save settings and colours"); save.clicked.connect(self.save_settings); reset = QPushButton("Reset colours"); reset.clicked.connect(self.reset_theme); settings_actions.addWidget(save); settings_actions.addWidget(reset); settings_actions.addStretch(); root.addLayout(settings_actions)
        exports = QHBoxLayout()
        for text, kind in (("Export tasks CSV", "tasks"), ("Export work CSV", "work"), ("Export pomodoros CSV", "pomodoros")):
            b = QPushButton(text); b.clicked.connect(lambda _=False, k=kind: self.export_csv(k)); exports.addWidget(b)
        root.addLayout(exports); root.addStretch()
    def update_color_button(self, key):
        color = self.theme[key]; self.color_buttons[key].setText(color); self.color_buttons[key].setStyleSheet(f"background:{color};color:{contrast_text(color)};padding:6px 12px")
    def choose_theme_color(self, key):
        selected = QColorDialog.getColor(QColor(self.theme[key]), self, f"Choose {key.replace('_', ' ')}")
        if selected.isValid(): self.theme[key] = selected.name(); self.update_color_button(key)
    def save_settings(self):
        self.db.set_setting("work_minutes", self.work_setting.value()); self.db.set_setting("rest_minutes", self.break_setting.value()); self.db.set_setting("lock_enabled", self.lock_setting.isChecked())
        self.theme["opacity"] = self.opacity_setting.value(); self.db.set_setting("theme", self.theme); self.setWindowOpacity(float(self.theme["opacity"])); self.apply_theme(); self.refresh_all()
    def reset_theme(self):
        self.theme = dict(DEFAULT_THEME); self.opacity_setting.setValue(float(self.theme["opacity"]))
        for key in self.color_buttons: self.update_color_button(key)
        self.db.set_setting("theme", self.theme); self.setWindowOpacity(float(self.theme["opacity"])); self.apply_theme(); self.refresh_all()
    def export_csv(self, kind):
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", f"pimodoro_{kind}.csv", "CSV (*.csv)")
        if not path: return
        if kind == "tasks": rows = self.db.all_tasks()
        elif kind == "work": rows = self.db.work_sessions()
        else: rows = self.db.all_pomodoros()
        if not rows: return
        with open(path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    def start_timer(self, mode):
        if self.timer_running and self.timer_mode == mode: self.pause_timer(); return
        self.timer_mode = mode; self.timer_remaining = self.duration_for(mode) * 60; self.timer_running = True; self.timer_started = datetime.now().astimezone(); self.refresh_timer_labels()
    def duration_for(self, mode):
        task = self.db.get_task(self.timer_task_id) if self.timer_task_id else None; key = "task_work_minutes" if mode == "work" else "task_break_minutes"
        return int(task.get(key)) if task else int(self.db.get_setting("work_minutes" if mode == "work" else "rest_minutes", 25 if mode == "work" else 5))
    def pause_timer(self): self.timer_running = False
    def reset_timer(self): self.timer_running = False; self.timer_remaining = self.duration_for(self.timer_mode) * 60; self.refresh_timer_labels()
    def refresh_timer_labels(self):
        self.timer_label.setText(f"{self.timer_remaining // 60:02d}:{self.timer_remaining % 60:02d}")
        task = self.db.get_task(self.timer_task_id) if self.timer_task_id else None; self.timer_task.setText(str(task["title"]) if task else "No task selected")
    def complete_timer(self):
        ended = datetime.now().astimezone(); planned = self.duration_for(self.timer_mode) * 60
        if self.timer_mode == "work":
            self.db.add_pomodoro_session(self.timer_task_id, (self.timer_started or ended).isoformat(), ended.isoformat(), planned, planned, True)
            if self.db.get_setting("lock_enabled", False): self.lock_screen()
        self.timer_running = False; self.timer_remaining = self.duration_for(self.timer_mode) * 60; self.refresh_all()
    def lock_screen(self):
        for command in (["cinnamon-screensaver-command", "--lock"], ["loginctl", "lock-session"], ["xdg-screensaver", "lock"]):
            if shutil.which(command[0]):
                try: subprocess.run(command, timeout=5, check=False); return
                except (OSError, subprocess.SubprocessError): pass

    def toggle_clock(self):
        if self.clock_started:
            end = datetime.now().astimezone(); self.db.add_work_session(self.clock_started.date().isoformat(), self.clock_started.isoformat(), end.isoformat(), int((end - self.clock_started).total_seconds())); self.clock_started = None
        else: self.clock_started = datetime.now().astimezone()
        self.refresh_all()
    def live_clock(self): return int((datetime.now().astimezone() - self.clock_started).total_seconds()) if self.clock_started else 0
    def every_second(self):
        now = datetime.now().astimezone(); self.header_date.setText(now.strftime("%A, %d %B %Y")); self.header_time.setText(now.strftime("%H:%M:%S"))
        total = self.db.work_seconds(date.today().isoformat()) + self.live_clock(); self.header_total.setText(f"Clocked {format_duration(total)}"); self.clock_total.setText(f"Today {format_duration(total)}"); self.clock_button.setText("Clock out" if self.clock_started else "Clock in")
        if self.timer_running:
            self.timer_remaining -= 1
            if self.timer_remaining <= 0: self.complete_timer()
            else: self.refresh_timer_labels()

    def refresh_all(self):
        self.refresh_today(); self.refresh_projects(); self.refresh_calendar_detail(); self.refresh_history(); self.refresh_timer_labels(); self.every_second()
        total = self.db.work_seconds(date.today().isoformat()) + self.live_clock(); self.work_total.setText(f"Total work today: {format_duration(total)}"); self.break_total.setText("Pomodoro timer")
    def closeEvent(self, event): self.save_graveyard(); self.db.close(); event.accept()


def main() -> int:
    app = QApplication(sys.argv); register_fonts(); window = PiModoro(); window.show(); return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
