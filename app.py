from __future__ import annotations

import calendar
import csv
import json
import math
import shutil
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Any, Iterable, Mapping

from pimodoro_db import Database, now_iso

APP_NAME = "PiModoro"
DB_FILE = Path.home() / ".pimodoro.db"

DEFAULT_THEME = {
    "background": "#023d2a",
    "panel": "#045c3d",
    "accent": "#05774a",
    "hover": "#07935c",
    "text": "#e8fff5",
    "muted": "#b1d8c7",
    "field": "#032f22",
    "note_paper": "#fffdf5",
    "note_text": "#1f2937",
    "graveyard_text": "#31445a",
    "P1": "#e5484d",
    "P2": "#f59e0b",
    "P3": "#3b82f6",
    "P4": "#94a3b8",
    "opacity": 0.94,
}

PRIORITY_LABELS = {
    "P1": "P1 Critical",
    "P2": "P2 High",
    "P3": "P3 Medium",
    "P4": "P4 Low",
}

TRACKING_LABELS = {
    "manual": "Manual time",
    "pomodoro": "Pomodoro progress",
    "both": "Both",
}


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def contrast_text(hex_color: str) -> str:
    clean = str(hex_color).lstrip("#")
    if len(clean) != 6:
        return "#ffffff"
    try:
        red, green, blue = (int(clean[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return "#ffffff"
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return "#111111" if luminance > 165 else "#ffffff"


def format_duration(total_seconds: int, include_seconds: bool = False) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if include_seconds:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours}h {minutes:02d}m"


def parse_date(value: str, *, blank_ok: bool = True) -> str | None:
    clean = value.strip()
    if not clean and blank_ok:
        return None
    try:
        return date.fromisoformat(clean).isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid date: {clean}. Use YYYY-MM-DD.") from exc


def parse_exceptions(value: str) -> list[tuple[str, str]]:
    ranges: list[tuple[str, str]] = []
    for raw in value.replace("\n", ",").split(","):
        item = raw.strip()
        if not item:
            continue
        if ".." in item:
            start_text, end_text = (part.strip() for part in item.split("..", 1))
        else:
            start_text = end_text = item
        start = parse_date(start_text, blank_ok=False)
        end = parse_date(end_text, blank_ok=False)
        assert start is not None and end is not None
        if end < start:
            start, end = end, start
        ranges.append((start, end))
    return ranges


def lock_screen() -> tuple[bool, str]:
    commands = [
        ["cinnamon-screensaver-command", "--lock"],
        ["cinnamon-screensaver-command", "-l"],
        ["xdg-screensaver", "lock"],
        ["loginctl", "lock-session"],
        ["dm-tool", "lock"],
        ["gnome-screensaver-command", "-l"],
    ]
    tried: list[str] = []
    for command in commands:
        if not shutil.which(command[0]):
            continue
        tried.append(" ".join(command))
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True, " ".join(command)
    if tried:
        return False, "Tried: " + ", ".join(tried)
    return False, "No supported Linux screen-lock command was found."


class TaskDialog(tk.Toplevel):
    """Flat, scrollable task editor that matches the main application background."""

    def __init__(
        self,
        parent: "PiModoro",
        task: Mapping[str, Any] | None = None,
        prefill: str = "",
        default_deadline: str | None = None,
        default_folder_id: int | None = None,
    ):
        super().__init__(parent)
        self.parent = parent
        self.task: dict[str, Any] = {}
        self.result: dict[str, Any] | None = None
        self.title("Edit task" if task else "Add task")
        self.geometry("720x760")
        self.minsize(620, 560)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.configure(background=parent.theme["background"], borderwidth=0, highlightthickness=0)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        shell = tk.Frame(
            self,
            background=parent.theme["background"],
            borderwidth=0,
            highlightthickness=0,
        )
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            shell,
            background=parent.theme["background"],
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
        )
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        form = tk.Frame(
            canvas,
            background=parent.theme["background"],
            borderwidth=0,
            highlightthickness=0,
            padx=22,
            pady=18,
        )
        form_window = canvas.create_window((0, 0), window=form, anchor="nw")

        def resize_form(event: tk.Event) -> None:
            canvas.itemconfigure(form_window, width=event.width)

        def update_scroll(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind("<Configure>", resize_form)
        form.bind("<Configure>", update_scroll)
        canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))
        form.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))

        default_work = int(parent.default_work_minutes.get())
        default_break = int(parent.default_break_minutes.get())
        self.title_var = tk.StringVar(value=prefill)
        self.priority_var = tk.StringVar(value="P4")
        self.work_var = tk.IntVar(value=default_work)
        self.break_var = tk.IntVar(value=default_break)
        self.tracking_var = tk.StringVar(value="both")
        self.deadline_var = tk.StringVar(value=default_deadline or "")
        self.folder_choices = parent.db.get_project_folders()
        self.folder_name_to_id = {str(item["name"]): int(item["id"]) for item in self.folder_choices}
        self.folder_id_to_name = {int(item["id"]): str(item["name"]) for item in self.folder_choices}
        self.folder_var = tk.StringVar(value=self.folder_id_to_name.get(int(default_folder_id), "No folder") if default_folder_id else "No folder")
        self.manual_hours_var = tk.IntVar(value=0)
        self.manual_minutes_var = tk.IntVar(value=0)
        self.pomo_estimate_var = tk.IntVar(value=0)
        self.pomo_completed_var = tk.IntVar(value=0)
        self.recurrence_enabled_var = tk.BooleanVar(value=False)
        self.recurrence_kind_var = tk.StringVar(value="days")
        self.recurrence_interval_var = tk.IntVar(value=1)
        self.recurrence_start_var = tk.StringVar(value=date.today().isoformat())
        self.recurrence_end_var = tk.StringVar(value="")
        self.recurrence_max_var = tk.StringVar(value="")
        self.weekday_vars = [tk.BooleanVar(value=False) for _ in range(7)]

        form.columnconfigure(1, weight=1)
        self.heading_label = tk.Label(
            form,
            text="Edit task" if task else "Add task",
            background=parent.theme["background"],
            foreground=parent.theme["text"],
            font=("TkDefaultFont", 18, "bold"),
            anchor="w",
        )
        self.heading_label.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 14))

        ttk.Label(form, text="Task").grid(row=1, column=0, sticky="w", pady=6)
        title_entry = ttk.Entry(form, textvariable=self.title_var)
        title_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=6)
        title_entry.focus_set()

        ttk.Label(form, text="Priority").grid(row=2, column=0, sticky="w", pady=6)

        priority_frame = tk.Frame(
            form,
            background=parent.theme["background"],
            borderwidth=0,
        )
        priority_frame.grid(row=2, column=1, sticky="w", pady=6)

        self.priority_buttons = {}

        selected_icons = {
            "P1": "🔴",
            "P2": "🟠",
            "P3": "🔵",
            "P4": "⚪",
        }

        priority_icons = {
            "P1": "🔥",
            "P2": "🐇",
            "P3": "🐢",
            "P4": "☕",
        }


        def select_priority(p):
            self.priority_var.set(p)

            for name, button in self.priority_buttons.items():
                if name == p:
                    button.config(text=selected_icons[name])
                else:
                    button.config(text=priority_icons[name])


        for index, priority in enumerate(("P1", "P2", "P3", "P4")):
            button = tk.Label(
                priority_frame,
                text=priority_icons[priority],
                font=("Noto Color Emoji", 16),
                background=parent.theme["background"],
                cursor="hand2",
            )

            button.bind(
                "<Button-1>",
                lambda event, p=priority: select_priority(p)
            )

            button.grid(row=0, column=index, padx=4)

            self.priority_buttons[priority] = button
        ttk.Label(form, text="Tracking").grid(row=2, column=2, sticky="e", padx=(18, 8), pady=6)
        ttk.Combobox(
            form,
            textvariable=self.tracking_var,
            values=list(TRACKING_LABELS),
            state="readonly",
            width=18,
        ).grid(row=2, column=3, sticky="w", pady=6)

        ttk.Label(form, text="Work minutes").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Spinbox(form, from_=1, to=720, textvariable=self.work_var, width=8).grid(row=3, column=1, sticky="w", pady=6)
        ttk.Label(form, text="Break minutes").grid(row=3, column=2, sticky="e", padx=(18, 8), pady=6)
        ttk.Spinbox(form, from_=1, to=720, textvariable=self.break_var, width=8).grid(row=3, column=3, sticky="w", pady=6)

        ttk.Label(form, text="Date").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.deadline_var, width=14).grid(row=4, column=1, sticky="w", pady=6)
        ttk.Label(form, text="Project folder").grid(row=4, column=2, sticky="e", padx=(18, 8), pady=6)
        ttk.Combobox(
            form,
            textvariable=self.folder_var,
            values=["No folder"] + [str(item["name"]) for item in self.folder_choices],
            state="readonly",
            width=18,
        ).grid(row=4, column=3, sticky="w", pady=6)

        ttk.Label(form, text="Notes").grid(row=5, column=0, columnspan=4, sticky="w", pady=(14, 5))
        self.notes_text = tk.Text(
            form,
            height=7,
            wrap="word",
            background=parent.theme["note_paper"],
            foreground=parent.theme["note_text"],
            insertbackground=parent.theme["note_text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=12,
            pady=10,
        )
        self.notes_text.grid(row=6, column=0, columnspan=4, sticky="ew")
        self.notes_text.insert("1.0", "")

        section = tk.Label(
            form,
            text="Task totals",
            background=parent.theme["background"],
            foreground=parent.theme["text"],
            font=("TkDefaultFont", 12, "bold"),
            anchor="w",
        )
        section.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(18, 8))
        ttk.Label(form, text="Manual time").grid(row=8, column=0, sticky="w")
        total_row = ttk.Frame(form)
        total_row.grid(row=8, column=1, columnspan=3, sticky="w")
        ttk.Spinbox(total_row, from_=0, to=9999, textvariable=self.manual_hours_var, width=6).grid(row=0, column=0)
        ttk.Label(total_row, text="h").grid(row=0, column=1, padx=(3, 8))
        ttk.Spinbox(total_row, from_=0, to=59, textvariable=self.manual_minutes_var, width=6).grid(row=0, column=2)
        ttk.Label(total_row, text="m").grid(row=0, column=3, padx=(3, 18))
        ttk.Label(total_row, text="Pomodoros").grid(row=0, column=4)
        ttk.Spinbox(total_row, from_=0, to=9999, textvariable=self.pomo_completed_var, width=6).grid(row=0, column=5, padx=(5, 3))
        ttk.Label(total_row, text="of").grid(row=0, column=6)
        ttk.Spinbox(total_row, from_=0, to=9999, textvariable=self.pomo_estimate_var, width=6).grid(row=0, column=7, padx=(3, 0))

        recurrence_heading = tk.Label(
            form,
            text="Recurrence",
            background=parent.theme["background"],
            foreground=parent.theme["text"],
            font=("TkDefaultFont", 12, "bold"),
            anchor="w",
        )
        recurrence_heading.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(18, 8))
        recurrence = tk.Frame(form, background=parent.theme["background"], borderwidth=0, highlightthickness=0)
        recurrence.grid(row=10, column=0, columnspan=4, sticky="ew")
        recurrence.columnconfigure(7, weight=1)
        ttk.Checkbutton(recurrence, text="Recurring", variable=self.recurrence_enabled_var).grid(row=0, column=0, sticky="w")
        ttk.Label(recurrence, text="Every").grid(row=0, column=1, padx=(16, 4))
        ttk.Spinbox(recurrence, from_=1, to=999, textvariable=self.recurrence_interval_var, width=5).grid(row=0, column=2)
        ttk.Combobox(recurrence, textvariable=self.recurrence_kind_var, values=("days", "weeks", "months"), state="readonly", width=9).grid(row=0, column=3, padx=(4, 14))
        ttk.Label(recurrence, text="Start").grid(row=0, column=4)
        ttk.Entry(recurrence, textvariable=self.recurrence_start_var, width=11).grid(row=0, column=5, padx=4)
        ttk.Label(recurrence, text="End").grid(row=0, column=6)
        ttk.Entry(recurrence, textvariable=self.recurrence_end_var, width=11).grid(row=0, column=7, sticky="w", padx=4)
        weekdays = ttk.Frame(recurrence)
        weekdays.grid(row=1, column=0, columnspan=8, sticky="w", pady=(8, 4))
        for index, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            ttk.Checkbutton(weekdays, text=name, variable=self.weekday_vars[index]).grid(row=0, column=index, padx=(0, 8))
        ttk.Label(recurrence, text="Max occurrences").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(recurrence, textvariable=self.recurrence_max_var, width=8).grid(row=2, column=1, sticky="w")
        ttk.Label(recurrence, text="Skip dates/ranges").grid(row=3, column=0, sticky="nw", pady=4)
        self.exceptions_text = tk.Text(
            recurrence,
            height=3,
            width=52,
            wrap="word",
            background=parent.theme["field"],
            foreground=parent.theme["text"],
            insertbackground=parent.theme["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=8,
            pady=6,
        )
        self.exceptions_text.grid(row=3, column=1, columnspan=7, sticky="ew", pady=4)
        current_exceptions: list[dict[str, Any]] = []
        self.exceptions_text.insert(
            "1.0",
            ", ".join(
                item["start_date"] if item["start_date"] == item["end_date"] else f"{item['start_date']}..{item['end_date']}"
                for item in current_exceptions
            ),
        )

        ttk.Label(form, text="Subtasks, one per line").grid(row=11, column=0, columnspan=4, sticky="w", pady=(16, 5))
        self.subtasks_text = tk.Text(
            form,
            height=5,
            wrap="word",
            background=parent.theme["field"],
            foreground=parent.theme["text"],
            insertbackground=parent.theme["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=8,
            pady=6,
        )
        self.subtasks_text.grid(row=12, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        if task:
            self.load_task(task)

        buttons = tk.Frame(
            shell,
            background=parent.theme["background"],
            borderwidth=0,
            highlightthickness=0,
            padx=18,
            pady=14,
        )
        buttons.grid(row=1, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=5)
        ttk.Button(buttons, text="Save", command=self._save).grid(row=0, column=1, padx=5)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-Return>", lambda _event: self._save())

        # Linux/Tk requires the Toplevel to be mapped before a modal grab.
        self.wait_visibility()
        self.grab_set()

    def load_task(self, task: Mapping[str, Any]) -> None:
        """Populate the already-built Add Task form with an existing task."""
        self.task = dict(task)
        self.title("Edit task")
        self.heading_label.configure(text="Edit task")

        def as_int(key: str, default: int = 0) -> int:
            try:
                return int(self.task.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        self.title_var.set(str(self.task.get("title") or ""))
        self.priority_var.set(str(self.task.get("priority") or "P4"))
        self.work_var.set(max(1, as_int("task_work_minutes", int(self.parent.default_work_minutes.get()))))
        self.break_var.set(max(1, as_int("task_break_minutes", int(self.parent.default_break_minutes.get()))))
        tracking = str(self.task.get("tracking_mode") or "both")
        self.tracking_var.set(tracking if tracking in TRACKING_LABELS else "both")
        self.deadline_var.set(str(self.task.get("deadline") or ""))
        folder_id = as_int("folder_id", 0)
        self.folder_var.set(self.folder_id_to_name.get(folder_id, "No folder"))
        manual_seconds = max(0, as_int("manual_seconds", 0))
        self.manual_hours_var.set(manual_seconds // 3600)
        self.manual_minutes_var.set((manual_seconds % 3600) // 60)
        self.pomo_estimate_var.set(max(0, as_int("pomodoro_estimate", 0)))
        self.pomo_completed_var.set(max(0, as_int("pomodoro_completed", 0)))
        self.recurrence_enabled_var.set(bool(as_int("recurrence_enabled", 0)))
        recurrence_kind = str(self.task.get("recurrence_kind") or "days")
        self.recurrence_kind_var.set(recurrence_kind if recurrence_kind in ("days", "weeks", "months") else "days")
        self.recurrence_interval_var.set(max(1, as_int("recurrence_interval", 1)))
        self.recurrence_start_var.set(str(self.task.get("recurrence_start") or date.today().isoformat()))
        self.recurrence_end_var.set(str(self.task.get("recurrence_end") or ""))
        recurrence_max = self.task.get("recurrence_max")
        self.recurrence_max_var.set("" if recurrence_max in (None, "", 0) else str(recurrence_max))

        selected_weekdays = {
            int(item)
            for item in str(self.task.get("recurrence_weekdays") or "").split(",")
            if item.strip().isdigit()
        }
        for index, variable in enumerate(self.weekday_vars):
            variable.set(index in selected_weekdays)

        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", str(self.task.get("notes") or ""))
        self.exceptions_text.delete("1.0", "end")
        task_id = as_int("id", 0)
        if task_id:
            current_exceptions = self.parent.db.get_exceptions(task_id)
            self.exceptions_text.insert(
                "1.0",
                ", ".join(
                    item["start_date"]
                    if item["start_date"] == item["end_date"]
                    else f"{item['start_date']}..{item['end_date']}"
                    for item in current_exceptions
                ),
            )
            self.subtasks_text.delete("1.0", "end")
            self.subtasks_text.insert(
                "1.0",
                "\n".join(item["text"] for item in self.parent.db.get_subtasks(task_id)),
            )

    def _save(self) -> None:
        try:
            title = " ".join(self.title_var.get().split())
            if not title:
                raise ValueError("Task title cannot be empty.")
            work_minutes = max(1, min(720, int(self.work_var.get())))
            break_minutes = max(1, min(720, int(self.break_var.get())))
            deadline = parse_date(self.deadline_var.get())
            recurrence_start = parse_date(self.recurrence_start_var.get()) if self.recurrence_enabled_var.get() else None
            recurrence_end = parse_date(self.recurrence_end_var.get()) if self.recurrence_enabled_var.get() else None
            recurrence_max_text = self.recurrence_max_var.get().strip()
            recurrence_max = int(recurrence_max_text) if recurrence_max_text else None
            if recurrence_max is not None and recurrence_max < 1:
                raise ValueError("Maximum occurrences must be at least 1.")
            exceptions = parse_exceptions(self.exceptions_text.get("1.0", "end-1c"))
            manual_seconds = max(0, int(self.manual_hours_var.get())) * 3600 + max(0, int(self.manual_minutes_var.get())) * 60
            weekdays = ",".join(str(index) for index, variable in enumerate(self.weekday_vars) if variable.get())
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.result = {
            "values": {
                "title": title,
                "notes": self.notes_text.get("1.0", "end-1c"),
                "priority": self.priority_var.get(),
                "tracking_mode": self.tracking_var.get(),
                "task_work_minutes": work_minutes,
                "task_break_minutes": break_minutes,
                "manual_seconds": manual_seconds,
                "pomodoro_estimate": max(0, int(self.pomo_estimate_var.get())),
                "pomodoro_completed": max(0, int(self.pomo_completed_var.get())),
                "deadline": deadline,
                "folder_id": self.folder_name_to_id.get(self.folder_var.get()),
                "recurrence_enabled": self.recurrence_enabled_var.get(),
                "recurrence_kind": self.recurrence_kind_var.get(),
                "recurrence_interval": max(1, int(self.recurrence_interval_var.get())),
                "recurrence_weekdays": weekdays,
                "recurrence_start": recurrence_start,
                "recurrence_end": recurrence_end,
                "recurrence_max": recurrence_max,
            },
            "exceptions": exceptions,
            "subtasks": [
                line.strip()
                for line in self.subtasks_text.get("1.0", "end-1c").splitlines()
                if line.strip()
            ],
        }
        self.destroy()


class BulkDefaultsDialog(tk.Toplevel):
    def __init__(self, parent: "PiModoro", count: int):
        super().__init__(parent)
        self.result: dict[str, Any] | None = None
        self.title(f"Add {count} tasks")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        self.priority_var = tk.StringVar(value="P4")
        self.work_var = tk.IntVar(value=int(parent.default_work_minutes.get()))
        self.break_var = tk.IntVar(value=int(parent.default_break_minutes.get()))
        ttk.Label(frame, text=f"Settings for all {count} tasks", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )
        ttk.Label(frame, text="Priority").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Combobox(frame, textvariable=self.priority_var, values=list(PRIORITY_LABELS), state="readonly", width=12).grid(
            row=1, column=1, sticky="w", pady=5
        )
        ttk.Label(frame, text="Work minutes").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Spinbox(frame, from_=1, to=720, textvariable=self.work_var, width=8).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Label(frame, text="Break minutes").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Spinbox(frame, from_=1, to=720, textvariable=self.break_var, width=8).grid(row=3, column=1, sticky="w", pady=5)
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Add", command=self._save).grid(row=0, column=1, padx=4)

    def _save(self) -> None:
        try:
            self.result = {
                "priority": self.priority_var.get(),
                "task_work_minutes": max(1, min(720, int(self.work_var.get()))),
                "task_break_minutes": max(1, min(720, int(self.break_var.get()))),
                "tracking_mode": "both",
            }
        except (ValueError, tk.TclError):
            return
        self.destroy()


class DurationDialog(tk.Toplevel):
    def __init__(self, parent: "PiModoro", title: str, work: int, break_minutes: int):
        super().__init__(parent)
        self.result: tuple[int, int] | None = None
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.work_var = tk.IntVar(value=work)
        self.break_var = tk.IntVar(value=break_minutes)
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0)
        ttk.Label(frame, text="Work minutes").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Spinbox(frame, from_=1, to=720, textvariable=self.work_var, width=8).grid(row=0, column=1, pady=6)
        ttk.Label(frame, text="Break minutes").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Spinbox(frame, from_=1, to=720, textvariable=self.break_var, width=8).grid(row=1, column=1, pady=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Save", command=self._save).grid(row=0, column=1, padx=4)

    def _save(self) -> None:
        try:
            self.result = (
                max(1, min(720, int(self.work_var.get()))),
                max(1, min(720, int(self.break_var.get()))),
            )
        except (ValueError, tk.TclError):
            return
        self.destroy()


class TotalTimeDialog(tk.Toplevel):
    def __init__(self, parent: "PiModoro", title: str, seconds: int, callback):
        super().__init__(parent)
        self.callback = callback
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0)
        self.hours_var = tk.IntVar(value=max(0, seconds) // 3600)
        self.minutes_var = tk.IntVar(value=(max(0, seconds) % 3600) // 60)
        ttk.Label(frame, text="Hours").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Spinbox(frame, from_=0, to=9999, textvariable=self.hours_var, width=8).grid(row=0, column=1, pady=6)
        ttk.Label(frame, text="Minutes").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Spinbox(frame, from_=0, to=59, textvariable=self.minutes_var, width=8).grid(row=1, column=1, pady=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Save", command=self._save).grid(row=0, column=1, padx=4)

    def _save(self) -> None:
        seconds = max(0, int(self.hours_var.get())) * 3600 + max(0, int(self.minutes_var.get())) * 60
        self.callback(seconds)
        self.destroy()


class ProjectFolderDialog(tk.Toplevel):
    def __init__(self, parent: "PiModoro", folder: Mapping[str, Any] | None = None):
        super().__init__(parent)
        self.parent = parent
        self.result: tuple[str, str] | None = None
        self.title("Edit project folder" if folder else "New project folder")
        self.transient(parent)
        self.configure(background=parent.theme["background"], borderwidth=0, highlightthickness=0)
        self.resizable(False, False)
        frame = tk.Frame(self, background=parent.theme["background"], padx=18, pady=18)
        frame.grid(row=0, column=0)
        self.name_var = tk.StringVar(value=str(folder.get("name", "")) if folder else "")
        self.color = str(folder.get("color", "#526d82")) if folder else "#526d82"
        ttk.Label(frame, text="Folder name").grid(row=0, column=0, sticky="w", pady=6)
        entry = ttk.Entry(frame, textvariable=self.name_var, width=30)
        entry.grid(row=0, column=1, padx=(8, 0), pady=6)
        entry.focus_set()
        ttk.Label(frame, text="Colour").grid(row=1, column=0, sticky="w", pady=6)
        self.color_button = tk.Button(
            frame, text=self.color, background=self.color, foreground="#ffffff",
            activebackground=self.color, activeforeground="#ffffff", relief="flat",
            borderwidth=0, padx=12, pady=6, command=self.choose_color,
        )
        self.color_button.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Save", command=self.save).grid(row=0, column=1, padx=4)
        self.bind("<Return>", lambda _e: self.save())
        self.wait_visibility()
        self.grab_set()

    def choose_color(self) -> None:
        chosen = colorchooser.askcolor(color=self.color, parent=self)[1]
        if chosen:
            self.color = chosen
            self.color_button.configure(text=chosen, background=chosen, activebackground=chosen)

    def save(self) -> None:
        name = " ".join(self.name_var.get().split())
        if not name:
            return
        self.result = (name, self.color)
        self.destroy()


class FolderPickerDialog(tk.Toplevel):
    def __init__(self, parent: "PiModoro", current_folder_id: int | None = None):
        super().__init__(parent)
        self.result: int | None | object = _NO_RESULT
        self.title("Assign project folder")
        self.transient(parent)
        self.configure(background=parent.theme["background"])
        folders = parent.db.get_project_folders()
        self.name_to_id = {str(item["name"]): int(item["id"]) for item in folders}
        id_to_name = {int(item["id"]): str(item["name"]) for item in folders}
        self.folder_var = tk.StringVar(value=id_to_name.get(int(current_folder_id or 0), "No folder"))
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0)
        ttk.Label(frame, text="Project folder").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Combobox(
            frame, textvariable=self.folder_var, values=["No folder"] + list(self.name_to_id),
            state="readonly", width=28,
        ).grid(row=1, column=0, sticky="ew", pady=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Assign", command=self.save).grid(row=0, column=1, padx=4)
        self.wait_visibility()
        self.grab_set()

    def save(self) -> None:
        self.result = self.name_to_id.get(self.folder_var.get())
        self.destroy()


_NO_RESULT = object()


class MoveTaskDialog(tk.Toplevel):
    def __init__(self, parent: "PiModoro", task: Mapping[str, Any]):
        super().__init__(parent)
        self.result: dict[str, Any] | None = None
        self.title("Move / schedule task")
        self.transient(parent)
        self.configure(background=parent.theme["background"])
        folders = parent.db.get_project_folders()
        self.name_to_id = {str(item["name"]): int(item["id"]) for item in folders}
        id_to_name = {int(item["id"]): str(item["name"]) for item in folders}
        current_id = int(task.get("folder_id") or 0)
        self.folder_var = tk.StringVar(value=id_to_name.get(current_id, "No folder"))
        current_date = task.get("recurrence_start") if task.get("recurrence_enabled") else task.get("deadline")
        self.date_var = tk.StringVar(value=str(current_date or ""))
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0)
        ttk.Label(frame, text="Project folder").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Combobox(
            frame, textvariable=self.folder_var, values=["No folder"] + list(self.name_to_id),
            state="readonly", width=28,
        ).grid(row=0, column=1, padx=(8, 0), pady=5)
        ttk.Label(frame, text="Calendar date").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.date_var, width=16).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=5)
        ttk.Label(frame, text="Blank removes a one-off task from the calendar.", style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 8))
        shortcuts = ttk.Frame(frame)
        shortcuts.grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Button(shortcuts, text="Today", command=lambda: self.date_var.set(date.today().isoformat())).grid(row=0, column=0, padx=(0, 5))
        ttk.Button(shortcuts, text="Clear date", command=lambda: self.date_var.set("")).grid(row=0, column=1, padx=5)
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Move", command=self.save).grid(row=0, column=1, padx=4)
        self.wait_visibility()
        self.grab_set()

    def save(self) -> None:
        try:
            scheduled = parse_date(self.date_var.get())
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.result = {"folder_id": self.name_to_id.get(self.folder_var.get()), "date": scheduled}
        self.destroy()


class PiModoro(tk.Tk):
    def __init__(self):
        super().__init__(className=APP_NAME)
        self.title(APP_NAME)
        self.geometry("1380x860")
        self.minsize(1120, 700)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

        self.db = Database(DB_FILE)
        self.db.archive_completed_before(date.today())
        self.last_housekeeping_date = date.today()
        self.theme = dict(DEFAULT_THEME)
        saved_theme = self.db.get_setting("theme", {})
        # Older builds could leave the theme JSON encoded more than once.
        # Normalize it here so saved colours are applied before any widget is built.
        for _ in range(2):
            if isinstance(saved_theme, str):
                try:
                    saved_theme = json.loads(saved_theme)
                except (TypeError, json.JSONDecodeError):
                    break
            else:
                break
        if isinstance(saved_theme, dict):
            self.theme.update({key: value for key, value in saved_theme.items() if key in self.theme})
            # Store the normalized value so subsequent launches do not depend on a re-save.
            self.db.set_setting("theme", self.theme)

        self.default_work_minutes = tk.IntVar(value=int(self.db.get_setting("work_minutes", 25)))
        self.default_break_minutes = tk.IntVar(value=int(self.db.get_setting("rest_minutes", 5)))
        self.auto_start = tk.BooleanVar(value=bool(self.db.get_setting("auto_start", False)))
        self.lock_enabled = tk.BooleanVar(value=bool(self.db.get_setting("lock_enabled", False)))

        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.pages: dict[str, ttk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.current_page = "tasks"
        self.selected_project_folder_id: int | None = None
        self.project_folder_records: dict[str, dict[str, Any]] = {}
        self.calendar_records: dict[str, dict[str, Any]] = {}
        self.selected_task_id: int | None = None
        self.history_records: dict[str, dict[str, Any]] = {}
        self.calendar_month = date.today().replace(day=1)
        self.selected_calendar_date = date.today()
        self.drag_item: str | None = None
        self.inline_editor: tk.Entry | None = None
        self.selected_task_ids_set: set[int] = set()
        self.expanded_task_ids: set[int] = set()
        self.task_cards: dict[int, tk.Frame] = {}
        self.priority_lanes: dict[str, tk.Frame] = {}
        self.task_card_order: list[int] = []
        self.task_priorities: dict[int, str] = {}
        self.task_fonts: list[tkfont.Font] = []
        self.accordion_editors: dict[int, tuple[tk.Text, tk.Text, tk.Label]] = {}
        self.last_selected_task_id: int | None = None
        self.drag_task_id: int | None = None
        self.drag_start_y = 0
        self.drag_moved = False

        self.clock_state = self._load_clock_state()
        self.timer_mode = "work"
        self.timer_running = False
        self.timer_remaining = max(1, int(self.default_work_minutes.get())) * 60
        self.timer_target_end: datetime | None = None
        self.timer_started_at: datetime | None = None
        self.timer_task_id: int | None = None
        self.timer_job: str | None = None
        self._load_timer_state()

        self._apply_theme()
        self._build_ui()
        self._bind_global_scroll_handlers()
        self.refresh_all()
        # Re-apply persisted colours after Tk has mapped the window, without rebuilding
        # the interface or stacking extra touchpad bindings.
        self.after_idle(self._reapply_persisted_theme)
        self._tick_header()
        if self.timer_running:
            self._timer_tick()

    # ---------- Theme ----------

    def _reapply_persisted_theme(self) -> None:
        """Force saved colours onto widgets once Tk has mapped the window."""
        self._apply_theme()
        color_map = {
            str(DEFAULT_THEME[key]).lower(): str(self.theme[key])
            for key in DEFAULT_THEME
            if key != "opacity" and key in self.theme and self.theme[key] != DEFAULT_THEME[key]
        }
        options = (
            "background", "foreground", "activebackground", "activeforeground",
            "insertbackground", "highlightbackground", "highlightcolor",
        )

        def recolor(widget: tk.Misc) -> None:
            updates: dict[str, str] = {}
            for option in options:
                try:
                    current = str(widget.cget(option)).lower()
                except (tk.TclError, TypeError):
                    continue
                replacement = color_map.get(current)
                if replacement is not None:
                    updates[option] = replacement
            if updates:
                try:
                    widget.configure(**updates)
                except tk.TclError:
                    pass
            for child in widget.winfo_children():
                recolor(child)

        recolor(self)
        if self.current_page in self.pages:
            self.show_page(self.current_page)

    def _apply_theme(self) -> None:
        theme = self.theme
        self.configure(background=theme["background"])
        try:
            self.attributes("-alpha", float(theme["opacity"]))
        except (tk.TclError, TypeError, ValueError):
            self.attributes("-alpha", 1.0)
        style = self.style
        style.configure(".", background=theme["background"], foreground=theme["text"])
        style.configure("TFrame", background=theme["background"])
        style.configure("Panel.TFrame", background=theme["panel"])
        style.configure("TLabel", background=theme["background"], foreground=theme["text"])
        style.configure("Panel.TLabel", background=theme["panel"], foreground=theme["text"])
        style.configure("Muted.TLabel", background=theme["background"], foreground=theme["muted"])
        style.configure("PanelMuted.TLabel", background=theme["panel"], foreground=theme["muted"])
        style.configure(
            "TButton",
            background=theme["accent"],
            foreground=theme["text"],
            borderwidth=0,
            padding=(11, 7),
        )
        style.map("TButton", background=[("active", theme["hover"]), ("pressed", theme["panel"])])
        style.configure("TEntry", fieldbackground=theme["field"], foreground=theme["text"])
        style.configure("TSpinbox", fieldbackground=theme["field"], foreground=theme["text"])
        style.configure("TCombobox", fieldbackground=theme["field"], foreground=theme["text"])
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", theme["field"])],
            foreground=[("readonly", theme["text"])],
        )
        style.configure("TCheckbutton", background=theme["background"], foreground=theme["text"])
        style.configure("TLabelframe", background=theme["background"], foreground=theme["text"])
        style.configure("TLabelframe.Label", background=theme["background"], foreground=theme["text"])
        style.configure(
            "Treeview",
            background=theme["field"],
            fieldbackground=theme["field"],
            foreground=theme["text"],
            rowheight=34,
            borderwidth=0,
        )
        style.configure("Treeview.Heading", background=theme["panel"], foreground=theme["text"])
        style.map("Treeview", background=[("selected", theme["accent"])])

    # ---------- Layout ----------

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_sidebar()
        content = ttk.Frame(self)
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        self._build_header(content)
        self.page_container = ttk.Frame(content)
        self.page_container.grid(row=1, column=0, sticky="nsew")
        self.page_container.columnconfigure(0, weight=1)
        self.page_container.rowconfigure(0, weight=1)

        for name in ("tasks", "projects", "calendar", "history", "graveyards", "settings"):
            frame = ttk.Frame(self.page_container)
            frame.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = frame
        self._build_tasks_page(self.pages["tasks"])
        self._build_projects_page(self.pages["projects"])
        self._build_calendar_page(self.pages["calendar"])
        self._build_history_page(self.pages["history"])
        self._build_graveyards_page(self.pages["graveyards"])
        self._build_settings_page(self.pages["settings"])
        self.show_page("tasks")

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self, background=self.theme["panel"], width=185)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        tk.Label(
            sidebar,
            text=APP_NAME,
            background=self.theme["panel"],
            foreground=self.theme["text"],
            font=("TkDefaultFont", 20, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(18, 20))
        labels = (
            ("tasks", "Today"),
            ("projects", "Project folders"),
            ("calendar", "Calendar"),
            ("history", "History"),
            ("graveyards", "Graveyards"),
            ("settings", "Settings"),
        )
        for key, label in labels:
            button = tk.Button(
                sidebar,
                text=label,
                anchor="w",
                relief="flat",
                borderwidth=0,
                padx=16,
                pady=10,
                background=self.theme["panel"],
                foreground=self.theme["text"],
                activebackground=self.theme["accent"],
                activeforeground=self.theme["text"],
                command=lambda page=key: self.show_page(page),
            )
            button.pack(fill="x")
            self.nav_buttons[key] = button

        spacer = tk.Frame(sidebar, background=self.theme["panel"])
        spacer.pack(fill="both", expand=True)
        self.clock_button = tk.Button(
            sidebar,
            text="Clock in",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=12,
            command=self.toggle_clock,
            foreground="#ffffff",
        )
        self.clock_button.pack(fill="x", padx=14, pady=(0, 8))
        self.sidebar_work_total = tk.Label(
            sidebar,
            text="Today 0h 00m",
            background=self.theme["panel"],
            foreground=self.theme["muted"],
        )
        self.sidebar_work_total.pack(fill="x", padx=14, pady=(0, 16))
        self._refresh_clock_button()

    def _build_header(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent, style="Panel.TFrame", padding=(18, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self.header_date = ttk.Label(header, text="", style="Panel.TLabel", font=("TkDefaultFont", 13, "bold"))
        self.header_date.grid(row=0, column=0, sticky="w")
        self.header_time = ttk.Label(header, text="", style="PanelMuted.TLabel", font=("TkDefaultFont", 12))
        self.header_time.grid(row=1, column=0, sticky="w")
        self.header_totals = ttk.Label(header, text="", style="Panel.TLabel", justify="right")
        self.header_totals.grid(row=0, column=1, rowspan=2, sticky="e")

    def show_page(self, name: str) -> None:
        if name not in self.pages:
            return
        if self.current_page == "graveyards" and name != "graveyards":
            self.save_graveyard_notes()
        self.current_page = name
        self.pages[name].tkraise()
        for key, button in self.nav_buttons.items():
            button.configure(background=self.theme["accent"] if key == name else self.theme["panel"])
        if name == "projects":
            self.refresh_project_folders()
        elif name == "calendar":
            self.refresh_calendar()
        elif name == "history":
            self.refresh_history()

    # ---------- Tasks page ----------

    def _build_tasks_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)

        timer = ttk.Frame(page, padding=(18, 14, 18, 8))
        timer.grid(row=0, column=0, sticky="ew")
        timer.columnconfigure(1, weight=1)
        self.work_duration_label = tk.Label(
            timer,
            text="Work 25m",
            background=self.theme["background"],
            foreground=self.theme["muted"],
            cursor="hand2",
            font=("TkDefaultFont", 11, "underline"),
        )
        self.work_duration_label.grid(row=0, column=0, sticky="e", padx=(0, 18))
        self.work_duration_label.bind("<Button-1>", lambda _event: self.edit_selected_durations())
        self.timer_label = ttk.Label(timer, text="25:00", font=("TkDefaultFont", 56, "bold"), anchor="center")
        self.timer_label.grid(row=0, column=1, sticky="ew")
        self.break_duration_label = tk.Label(
            timer,
            text="Break 5m",
            background=self.theme["background"],
            foreground=self.theme["muted"],
            cursor="hand2",
            font=("TkDefaultFont", 11, "underline"),
        )
        self.break_duration_label.grid(row=0, column=2, sticky="w", padx=(18, 0))
        self.break_duration_label.bind("<Button-1>", lambda _event: self.edit_selected_durations())
        self.timer_task_label = ttk.Label(timer, text="No task selected", style="Muted.TLabel", anchor="center")
        self.timer_task_label.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        controls = ttk.Frame(timer)
        controls.grid(row=2, column=0, columnspan=3)
        ttk.Button(controls, text="Work", command=lambda: self.start_timer_mode("work")).grid(row=0, column=0, padx=4)
        ttk.Button(controls, text="Break", command=lambda: self.start_timer_mode("break")).grid(row=0, column=1, padx=4)
        ttk.Button(controls, text="Reset", command=self.reset_timer).grid(row=0, column=2, padx=4)
        ttk.Button(controls, text="Edit", command=self.edit_selected_task).grid(row=0, column=3, padx=4)

        addbar = ttk.Frame(page, padding=(18, 2, 18, 8))
        addbar.grid(row=1, column=0, sticky="ew")
        addbar.columnconfigure(0, weight=1)
        self.quick_add_var = tk.StringVar()
        self.quick_add_entry = ttk.Entry(addbar, textvariable=self.quick_add_var)
        self.quick_add_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.quick_add_entry.bind("<Return>", lambda _event: self.open_add_dialog(default_deadline=date.today().isoformat()))
        self.quick_add_entry.bind("<<Paste>>", self._quick_paste)
        ttk.Button(addbar, text="Add task", command=lambda: self.open_add_dialog(default_deadline=date.today().isoformat())).grid(row=0, column=1)

        list_frame = ttk.Frame(page, padding=(18, 0, 18, 14))
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.task_canvas = tk.Canvas(
            list_frame,
            background=self.theme["background"],
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
        )
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.task_canvas.yview)
        self.task_canvas.configure(yscrollcommand=scrollbar.set)
        self.task_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.task_list_inner = tk.Frame(
            self.task_canvas,
            background=self.theme["background"],
            borderwidth=0,
            highlightthickness=0,
        )
        self.task_list_window = self.task_canvas.create_window((0, 0), window=self.task_list_inner, anchor="nw")
        self.task_list_inner.bind("<Configure>", self._update_task_scrollregion)
        self.task_canvas.bind(
            "<Configure>",
            lambda event: self.task_canvas.itemconfigure(self.task_list_window, width=event.width),
        )

        actions = ttk.Frame(list_frame)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tk.Button(
            actions,
            text="Archive selected",
            command=self.archive_selected_tasks,
            background=self.theme["panel"],
            foreground=self.theme["muted"],
            activebackground=self.theme["hover"],
            activeforeground=self.theme["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=5,
            pady=1,
            font=("TkDefaultFont", 8),
            cursor="hand2",
        ).grid(row=0, column=0, padx=(0, 4))
        tk.Button(
            actions,
            text="Delete",
            command=self.delete_selected_tasks,
            background=self.theme["panel"],
            foreground=self.theme["muted"],
            activebackground=self.theme["hover"],
            activeforeground=self.theme["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=5,
            pady=1,
            font=("TkDefaultFont", 8),
            cursor="hand2",
        ).grid(row=0, column=1, padx=(0, 6))
        self.selection_hint = ttk.Label(
            actions,
            text="Ctrl-click selects several tasks. Drag a task card to reorder.",
            style="Muted.TLabel",
        )
        self.selection_hint.grid(row=0, column=2, sticky="e", padx=(18, 0))
        actions.columnconfigure(2, weight=1)

    def _bind_global_scroll_handlers(self) -> None:
        """Bind wheel/touchpad events once; theme rebuilds must not stack handlers."""
        self.bind_all("<MouseWheel>", self._on_global_mousewheel)
        self.bind_all("<Button-4>", self._on_global_mousewheel)
        self.bind_all("<Button-5>", self._on_global_mousewheel)

    def _on_global_mousewheel(self, event: tk.Event) -> str | None:
        if self.current_page == "tasks":
            return self._on_task_mousewheel(event)
        if self.current_page == "graveyards":
            return self._on_graveyard_mousewheel(event)
        return None

    def _pointer_over_task_list(self) -> bool:
        if self.current_page != "tasks" or not hasattr(self, "task_canvas"):
            return False
        try:
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            left = self.task_canvas.winfo_rootx()
            top = self.task_canvas.winfo_rooty()
            return left <= x < left + self.task_canvas.winfo_width() and top <= y < top + self.task_canvas.winfo_height()
        except tk.TclError:
            return False

    def _update_task_scrollregion(self, _event: tk.Event | None = None) -> None:
        """Keep the Today canvas scroll range anchored exactly at its top edge."""
        if not hasattr(self, "task_canvas") or not hasattr(self, "task_list_inner"):
            return
        self.update_idletasks()
        width = max(self.task_canvas.winfo_width(), self.task_list_inner.winfo_reqwidth())
        height = max(1, self.task_list_inner.winfo_reqheight())
        self.task_canvas.configure(scrollregion=(0, 0, width, height))
        first, _last = self.task_canvas.yview()
        if first < 0.0001:
            self.task_canvas.yview_moveto(0.0)

    def _on_task_mousewheel(self, event: tk.Event) -> str | None:
        """Support Linux touchpad/wheel scrolling without overshooting the top."""
        if not self._pointer_over_task_list():
            return None

        number = getattr(event, "num", None)
        if number == 4:
            direction = -1
        elif number == 5:
            direction = 1
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return None
            direction = -1 if delta > 0 else 1

        first, last = self.task_canvas.yview()
        visible = max(0.01, last - first)
        max_first = max(0.0, 1.0 - visible)
        # A small fixed fraction feels closer to webpage touchpad scrolling and
        # prevents large Linux wheel deltas from jumping past the content edge.
        target = min(max_first, max(0.0, first + direction * 0.035))
        self.task_canvas.yview_moveto(target)
        return "break"

    def render_tasks(self, preserve_selection: Iterable[int] = ()) -> None:
        self.save_open_accordions()
        self.selected_task_ids_set.update(int(item) for item in preserve_selection)
        for child in self.task_list_inner.winfo_children():
            child.destroy()
        self.task_cards.clear()
        self.priority_lanes.clear()
        self.task_card_order.clear()
        self.task_priorities.clear()
        self.task_fonts.clear()
        self.accordion_editors.clear()

        tasks = self.db.get_active_tasks(date.today())
        valid_ids = {int(task["id"]) for task in tasks}
        self.selected_task_ids_set.intersection_update(valid_ids)
        if self.selected_task_id not in valid_ids:
            self.selected_task_id = next(iter(self.selected_task_ids_set), None)

        # Today is intentionally a four-column priority board.  A task can also
        # belong to a project folder; folder membership never removes its date.
        for column, priority in enumerate(("P1", "P2", "P3", "P4")):
            self.task_list_inner.columnconfigure(column, weight=1, uniform="priority")
            lane = tk.Frame(
                self.task_list_inner,
                background=self.theme["background"],
                borderwidth=0,
                highlightthickness=0,
                padx=4,
            )
            lane.grid(row=0, column=column, sticky="nsew")
            self.priority_lanes[priority] = lane
            tk.Label(
                lane,
                text=PRIORITY_LABELS[priority],
                background=self.theme["background"],
                foreground=self.theme[priority],
                font=("TkDefaultFont", 10, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(0, 8))
            lane_tasks = [task for task in tasks if str(task.get("priority", "P4")) == priority]
            if not lane_tasks:
                tk.Label(
                    lane,
                    text="No tasks",
                    background=self.theme["background"],
                    foreground=self.theme["muted"],
                    anchor="w",
                    font=("TkDefaultFont", 9),
                ).pack(fill="x", pady=(4, 0))
                continue
            for task in lane_tasks:
                self._build_task_card(task, lane)

        if not tasks:
            # Keep the four priority columns visible even on an empty day.
            self.selected_task_id = None

        self._refresh_task_card_selection()
        self._update_timer_labels()

    def _build_task_card(self, task: Mapping[str, Any], parent: tk.Widget | None = None) -> None:
        task_id = int(task["id"])
        priority = str(task.get("priority", "P4"))
        self.task_card_order.append(task_id)
        self.task_priorities[task_id] = priority
        background = self.theme.get(priority, self.theme["field"])
        foreground = contrast_text(background)
        done = bool(task.get("display_done") or task.get("status") == "completed")
        parent = parent or self.task_list_inner

        card = tk.Frame(
            parent,
            background=background,
            borderwidth=0,
            highlightthickness=2,
            highlightbackground=background,
            highlightcolor=self.theme["accent"],
        )
        # Use most of the priority lane horizontally while keeping the card vertically thin.
        card.pack(fill="x", padx=(0, 3), pady=(0, 4))
        self.task_cards[task_id] = card

        row = tk.Frame(card, background=background, borderwidth=0, highlightthickness=0, padx=3, pady=2)
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)

        circle = tk.Button(
            row,
            text="●" if done else "○",
            command=lambda item=task_id: self.toggle_task_done(item),
            background=background,
            foreground=foreground,
            activebackground=background,
            activeforeground=foreground,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("TkDefaultFont", 16, "bold"),
            cursor="hand2",
            padx=3,
            pady=0,
        )
        circle.grid(row=0, column=0, sticky="n")

        title_font = tkfont.Font(font=("TkDefaultFont", 9, "bold"))
        title_font.configure(overstrike=done)
        self.task_fonts.append(title_font)
        title = tk.Label(
            row,
            text=" ".join(str(task["title"]).split()),
            background=background,
            foreground=foreground,
            font=title_font,
            anchor="w",
            justify="left",
            wraplength=210,
            cursor="xterm",
            padx=2,
            pady=1,
        )
        title.grid(row=0, column=1, sticky="w")
        title.bind("<Button-1>", lambda event, item=task_id, widget=title: self.begin_card_title_edit(item, widget, event))

        notes_button = tk.Button(
            row,
            text="Notes ▾" if task_id in self.expanded_task_ids else "Notes ▸",
            command=lambda item=task_id: self.toggle_task_accordion(item),
            background=background,
            foreground=foreground,
            activebackground=background,
            activeforeground=foreground,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
            padx=3,
            pady=1,
        )
        notes_button.grid(row=1, column=1, sticky="w", padx=(0, 3), pady=0)

        timer_button = tk.Button(
            row,
            text=f"{int(task.get('task_work_minutes', 25))}/{int(task.get('task_break_minutes', 5))} min",
            command=lambda item=task_id: self.edit_task_durations(item),
            background=background,
            foreground=foreground,
            activebackground=background,
            activeforeground=foreground,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
            padx=3,
            pady=1,
        )
        timer_button.grid(row=1, column=2, sticky="w", padx=(2, 1), pady=0)

        for widget in (card, row):
            widget.bind("<ButtonPress-1>", lambda event, item=task_id: self._card_press(item, event))
            widget.bind("<B1-Motion>", self._card_motion)
            widget.bind("<ButtonRelease-1>", self._card_release)
            widget.bind("<Double-1>", lambda _event, item=task_id: self.edit_task(item))

        if task_id in self.expanded_task_ids:
            self._build_task_accordion(card, task, background, foreground)

    def _build_task_accordion(
        self,
        card: tk.Frame,
        task: Mapping[str, Any],
        card_background: str,
        card_foreground: str,
    ) -> None:
        task_id = int(task["id"])
        details = tk.Frame(
            card,
            background=self.theme["field"],
            borderwidth=0,
            highlightthickness=0,
            padx=8,
            pady=8,
        )
        details.pack(fill="x", padx=4, pady=(0, 5))
        details.columnconfigure(0, weight=1)

        tk.Label(
            details, text="Notes", background=self.theme["field"],
            foreground=self.theme["text"], anchor="w",
        ).grid(row=0, column=0, sticky="w")
        notes = tk.Text(
            details, height=5, width=25, wrap="word",
            background=self.theme["note_paper"], foreground=self.theme["note_text"],
            insertbackground=self.theme["note_text"], relief="flat", borderwidth=0,
            highlightthickness=0, padx=7, pady=6,
        )
        notes.grid(row=1, column=0, sticky="ew", pady=(4, 7))
        notes.insert("1.0", str(task.get("notes", "")))

        tk.Label(
            details, text="Subtasks, one per line", background=self.theme["field"],
            foreground=self.theme["text"], anchor="w",
        ).grid(row=2, column=0, sticky="w")
        subtasks = tk.Text(
            details, height=4, width=25, wrap="word",
            background=self.theme["background"], foreground=self.theme["text"],
            insertbackground=self.theme["text"], relief="flat", borderwidth=0,
            highlightthickness=0, padx=7, pady=6,
        )
        subtasks.grid(row=3, column=0, sticky="ew", pady=(4, 7))
        subtasks.insert("1.0", "\n".join(item["text"] for item in self.db.get_subtasks(task_id)))

        metadata_parts = [
            TRACKING_LABELS.get(str(task.get("tracking_mode", "both")), str(task.get("tracking_mode", "both"))),
        ]
        if task.get("deadline"):
            metadata_parts.append(f"Due {task['deadline']}")
        if task.get("folder_id"):
            folder = self.db.get_project_folder(int(task["folder_id"]))
            if folder:
                metadata_parts.append(str(folder["name"]))
        if task.get("recurrence_enabled"):
            metadata_parts.append(f"Every {task.get('recurrence_interval', 1)} {task.get('recurrence_kind', 'days')}")
        metadata = tk.Label(
            details, text=" · ".join(metadata_parts), background=self.theme["field"],
            foreground=self.theme["muted"], anchor="w", justify="left", wraplength=210,
        )
        metadata.grid(row=4, column=0, sticky="ew", pady=(2, 6))

        actions = tk.Frame(details, background=self.theme["field"], borderwidth=0, highlightthickness=0)
        actions.grid(row=5, column=0, sticky="w")
        ttk.Button(actions, text="Save", command=lambda item=task_id: self.save_task_accordion(item)).grid(row=0, column=0, padx=(0, 3))
        ttk.Button(actions, text="Edit", command=lambda item=task_id: self.edit_task(item)).grid(row=0, column=1, padx=3)
        ttk.Button(actions, text="Move", command=lambda item=task_id: self.move_task_dialog(item)).grid(row=0, column=2, padx=3)
        status = tk.Label(actions, text="", background=self.theme["field"], foreground=self.theme["muted"])
        status.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.accordion_editors[task_id] = (notes, subtasks, status)

    def selected_task_ids(self) -> list[int]:
        return [task_id for task_id in self.task_card_order if task_id in self.selected_task_ids_set]

    def _refresh_task_card_selection(self) -> None:
        for task_id, card in self.task_cards.items():
            selected = task_id in self.selected_task_ids_set
            card.configure(
                highlightbackground=self.theme["accent"] if selected else card.cget("background"),
                highlightthickness=3 if selected else 2,
            )

    def select_task_card(self, task_id: int, event: tk.Event | None = None) -> None:
        state = int(getattr(event, "state", 0)) if event is not None else 0
        control = bool(state & 0x0004)
        shift = bool(state & 0x0001)
        if shift and self.last_selected_task_id in self.task_card_order:
            start = self.task_card_order.index(self.last_selected_task_id)
            end = self.task_card_order.index(task_id)
            low, high = sorted((start, end))
            self.selected_task_ids_set.update(self.task_card_order[low : high + 1])
        elif control:
            if task_id in self.selected_task_ids_set:
                self.selected_task_ids_set.remove(task_id)
            else:
                self.selected_task_ids_set.add(task_id)
        else:
            self.selected_task_ids_set = {task_id}
        if task_id in self.selected_task_ids_set:
            self.selected_task_id = task_id
            self.last_selected_task_id = task_id
            if not self.timer_running:
                self.timer_task_id = task_id
                self.timer_remaining = self._task_duration_seconds(task_id, self.timer_mode)
                self.timer_started_at = None
                self._save_timer_state()
        elif self.selected_task_id == task_id:
            self.selected_task_id = next(iter(self.selected_task_ids_set), None)
        self._refresh_task_card_selection()
        self._update_timer_labels()

    def on_task_selection(self, _event: tk.Event | None = None) -> None:
        if self.selected_task_id is not None:
            self.select_task_card(self.selected_task_id)

    def open_add_dialog(
        self,
        prefill: str | None = None,
        default_deadline: str | None = None,
        default_folder_id: int | None = None,
    ) -> None:
        text = self.quick_add_var.get() if prefill is None else prefill
        if default_deadline is None and self.current_page == "tasks":
            default_deadline = date.today().isoformat()
        if "\n" in text or "\r" in text:
            self.handle_pasted_text(text)
            return
        dialog = TaskDialog(
            self,
            prefill=" ".join(text.split()),
            default_deadline=default_deadline,
            default_folder_id=default_folder_id,
        )
        self.wait_window(dialog)
        if not dialog.result:
            return
        task_id = self.db.create_task(dialog.result["values"] | {"subtasks": dialog.result["subtasks"]})
        self.db.set_exceptions(task_id, dialog.result["exceptions"])
        self.quick_add_var.set("")
        self.selected_task_id = task_id
        self.selected_task_ids_set = {task_id}
        self.timer_task_id = task_id
        self.refresh_all()

    def _quick_paste(self, _event: tk.Event) -> str | None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return None
        if "\n" not in text and "\r" not in text:
            return None
        self.after_idle(lambda: self.handle_pasted_text(text))
        return "break"

    def handle_pasted_text(self, text: str) -> None:
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        if not lines:
            return
        if len(lines) == 1:
            self.open_add_dialog(lines[0])
            return
        choice = messagebox.askyesnocancel(
            "Paste list",
            f"The pasted text contains {len(lines)} lines.\n\nYes: add each line as a separate task.\nNo: add everything as one task.",
            parent=self,
        )
        if choice is None:
            return
        if choice is False:
            self.open_add_dialog(" ".join(lines))
            return
        dialog = BulkDefaultsDialog(self, len(lines))
        self.wait_window(dialog)
        if not dialog.result:
            return
        created: list[int] = []
        for line in lines:
            created.append(self.db.create_task({"title": line, "deadline": date.today().isoformat()} | dialog.result))
        self.quick_add_var.set("")
        if hasattr(self, "add_text_widget"):
            self.add_text_widget.delete("1.0", "end")
        self.selected_task_id = created[0]
        self.selected_task_ids_set = set(created)
        self.timer_task_id = created[0]
        self.refresh_all()

    def edit_selected_task(self) -> None:
        ids = self.selected_task_ids()
        task_id = ids[0] if ids else self.selected_task_id
        if task_id is None:
            return
        self.edit_task(task_id)

    def edit_task(self, task_id: int) -> None:
        self.save_open_accordions()
        task = self.db.get_task(task_id)
        if not task:
            return
        dialog = TaskDialog(self, task=task)
        self.wait_window(dialog)
        if not dialog.result:
            return
        self.db.update_task(task_id, dialog.result["values"])
        self.db.set_exceptions(task_id, dialog.result["exceptions"])
        self.db.replace_subtasks(task_id, dialog.result["subtasks"])
        self.selected_task_id = task_id
        self.selected_task_ids_set = {task_id}
        self.refresh_all()

    def move_task_dialog(self, task_id: int) -> None:
        task = self.db.get_task(task_id)
        if not task:
            return
        dialog = MoveTaskDialog(self, task)
        self.wait_window(dialog)
        if not dialog.result:
            return
        values: dict[str, Any] = {"folder_id": dialog.result["folder_id"]}
        if task.get("recurrence_enabled"):
            if dialog.result["date"]:
                values["recurrence_start"] = dialog.result["date"]
        else:
            values["deadline"] = dialog.result["date"]
        self.db.update_task(task_id, values)
        self.selected_task_id = task_id
        self.refresh_all()

    def begin_card_title_edit(self, task_id: int, label: tk.Label, event: tk.Event | None = None) -> str:
        self.select_task_card(task_id, event)
        if self.inline_editor is not None and self.inline_editor.winfo_exists():
            self.inline_editor.destroy()
        parent = label.master
        original = label.cget("text")
        label.grid_remove()
        editor = tk.Entry(
            parent,
            background=self.theme["note_paper"],
            foreground=self.theme["note_text"],
            insertbackground=self.theme["note_text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("TkDefaultFont", 10, "bold"),
        )
        editor.insert(0, original)
        editor.select_range(0, "end")
        editor.grid(row=0, column=1, sticky="w", padx=3, pady=6)
        editor.focus_set()
        self.inline_editor = editor
        saved = False

        def finish(save: bool) -> None:
            nonlocal saved
            if saved:
                return
            saved = True
            new_title = " ".join(editor.get().split())
            if save and new_title:
                self.db.update_task(task_id, {"title": new_title})
            if editor.winfo_exists():
                editor.destroy()
            self.inline_editor = None
            self.render_tasks([task_id])

        editor.bind("<Return>", lambda _event: finish(True))
        editor.bind("<FocusOut>", lambda _event: finish(True))
        editor.bind("<Escape>", lambda _event: finish(False))
        return "break"

    def begin_inline_edit(self, event: tk.Event) -> None:
        return

    def toggle_task_done(self, task_id: int) -> None:
        self.save_open_accordions()
        task = self.db.get_task(task_id)
        if not task:
            return
        done = bool(task.get("status") == "completed")
        if task.get("recurrence_enabled"):
            today_items = self.db.calendar_tasks_for_range(date.today(), date.today()).get(date.today().isoformat(), [])
            done = any(int(item["id"]) == task_id and item.get("occurrence_completed") for item in today_items)
        if done:
            self.db.reopen_task(task_id, date.today())
        else:
            self.db.complete_task(task_id, date.today())
        self.db.archive_completed_before(date.today())
        self.selected_task_id = task_id
        self.selected_task_ids_set = {task_id}
        self.refresh_all()

    def complete_selected_tasks(self) -> None:
        for task_id in self.selected_task_ids():
            self.db.complete_task(task_id, date.today())
        self.db.archive_completed_before(date.today())
        self.refresh_all()

    def archive_selected_tasks(self) -> None:
        ids = self.selected_task_ids()
        if not ids:
            return
        if not messagebox.askyesno(
            "Archive tasks",
            f"Archive {len(ids)} selected task{'s' if len(ids) != 1 else ''}? They remain searchable in History.",
            parent=self,
        ):
            return
        self.save_open_accordions()
        for task_id in ids:
            self.db.archive_task(task_id)
        self.selected_task_ids_set.difference_update(ids)
        if self.selected_task_id in ids:
            self.selected_task_id = None
        self.refresh_all()

    def delete_selected_tasks(self) -> None:
        ids = self.selected_task_ids()
        if not ids:
            return
        count = len(ids)
        if not messagebox.askyesno(
            "Delete permanently",
            f"Permanently delete {count} selected task{'s' if count != 1 else ''}? This cannot be undone.",
            parent=self,
        ):
            return
        self.save_open_accordions()
        for task_id in ids:
            self.db.delete_task(task_id)
        if self.timer_task_id in ids:
            self.pause_timer()
            self.timer_task_id = None
        self.selected_task_ids_set.difference_update(ids)
        if self.selected_task_id in ids:
            self.selected_task_id = None
        self.refresh_all()

    def toggle_task_accordion(self, task_id: int) -> None:
        self.save_open_accordions()
        if task_id in self.expanded_task_ids:
            self.expanded_task_ids.remove(task_id)
        else:
            self.expanded_task_ids.add(task_id)
        self.selected_task_id = task_id
        self.selected_task_ids_set = {task_id}
        self.render_tasks([task_id])

    def save_task_accordion(self, task_id: int) -> None:
        editors = self.accordion_editors.get(task_id)
        if not editors:
            return
        notes, subtasks, status = editors
        self.db.update_task(task_id, {"notes": notes.get("1.0", "end-1c")})
        self.db.replace_subtasks(
            task_id,
            [line.strip() for line in subtasks.get("1.0", "end-1c").splitlines() if line.strip()],
        )
        status.configure(text="Saved")
        self.refresh_history()

    def save_open_accordions(self) -> None:
        for task_id, editors in list(getattr(self, "accordion_editors", {}).items()):
            try:
                notes, subtasks, _status = editors
                if not notes.winfo_exists() or not subtasks.winfo_exists():
                    continue
                self.db.update_task(task_id, {"notes": notes.get("1.0", "end-1c")})
                self.db.replace_subtasks(
                    task_id,
                    [line.strip() for line in subtasks.get("1.0", "end-1c").splitlines() if line.strip()],
                )
            except tk.TclError:
                continue

    def edit_task_durations(self, task_id: int) -> None:
        self.select_task_card(task_id)
        self.edit_selected_durations()

    def _card_press(self, task_id: int, event: tk.Event) -> None:
        self.select_task_card(task_id, event)
        self.drag_task_id = task_id
        self.drag_start_y = event.y_root
        self.drag_moved = False

    def _card_motion(self, event: tk.Event) -> None:
        if self.drag_task_id is None:
            return
        if abs(event.y_root - self.drag_start_y) >= 8:
            self.drag_moved = True

    def _card_release(self, event: tk.Event) -> None:
        task_id = self.drag_task_id
        self.drag_task_id = None
        if task_id is None or not self.drag_moved or task_id not in self.task_card_order:
            return
        target_id = task_id
        target_distance: int | None = None
        source_priority = self.task_priorities.get(task_id)

        # Dropping over another priority column changes the task priority.
        dropped_priority = None
        x_root = event.x_root
        for priority, lane in self.priority_lanes.items():
            left = lane.winfo_rootx()
            right = left + lane.winfo_width()
            if left <= x_root <= right:
                dropped_priority = priority
                break

        if dropped_priority and dropped_priority != source_priority:
            self.db.update_task(task_id, {"priority": dropped_priority})
            self.db.set_task_order([item for item in self.task_card_order if item != task_id])
            self.render_tasks(self.selected_task_ids_set)
            return

        for candidate_id, card in self.task_cards.items():
            if self.task_priorities.get(candidate_id) != source_priority:
                continue
            midpoint = card.winfo_rooty() + card.winfo_height() // 2
            distance = abs(event.y_root - midpoint)
            if target_distance is None or distance < target_distance:
                target_distance = distance
                target_id = candidate_id
        if target_id == task_id:
            return
        order = list(self.task_card_order)
        order.remove(task_id)
        target_index = order.index(target_id)
        target_card = self.task_cards[target_id]
        if event.y_root > target_card.winfo_rooty() + target_card.winfo_height() // 2:
            target_index += 1
        order.insert(target_index, task_id)
        self.db.set_task_order(order)
        self.render_tasks(self.selected_task_ids_set)

    def _drag_start(self, event: tk.Event) -> None:
        return

    def _drag_motion(self, event: tk.Event) -> None:
        return

    def _drag_end(self, _event: tk.Event) -> None:
        return

    # ---------- Add text page ----------

    def _build_add_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        ttk.Label(
            page,
            text="Paste or type tasks",
            font=("TkDefaultFont", 16, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(20, 8))
        self.add_text_widget = tk.Text(
            page,
            wrap="word",
            background=self.theme["note_paper"],
            foreground=self.theme["note_text"],
            insertbackground=self.theme["note_text"],
            relief="flat",
            padx=14,
            pady=14,
        )
        self.add_text_widget.grid(row=1, column=0, sticky="nsew", padx=20, pady=8)
        actions = ttk.Frame(page, padding=(20, 4, 20, 18))
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="Add text", command=lambda: self.handle_pasted_text(self.add_text_widget.get("1.0", "end-1c"))).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(actions, text="Clear", command=lambda: self.add_text_widget.delete("1.0", "end")).grid(row=0, column=1)
        ttk.Label(
            actions,
            text="Multiline text asks whether each line is a separate task or the entire paste is one task.",
            style="Muted.TLabel",
        ).grid(row=0, column=2, sticky="w", padx=(16, 0))

    # ---------- Details page ----------

    def _build_details_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(3, weight=1)
        self.details_heading = ttk.Label(page, text="Select a task", font=("TkDefaultFont", 16, "bold"))
        self.details_heading.grid(row=0, column=0, sticky="w", padx=20, pady=(20, 10))
        title_frame = ttk.Frame(page, padding=(20, 0, 20, 8))
        title_frame.grid(row=1, column=0, sticky="ew")
        title_frame.columnconfigure(0, weight=1)
        self.details_title_var = tk.StringVar()
        self.details_title_entry = ttk.Entry(title_frame, textvariable=self.details_title_var)
        self.details_title_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(title_frame, text="Advanced edit", command=self.edit_selected_task).grid(row=0, column=1)

        durations = ttk.Frame(page, padding=(20, 0, 20, 8))
        durations.grid(row=2, column=0, sticky="ew")
        self.details_duration_label = ttk.Label(durations, text="")
        self.details_duration_label.grid(row=0, column=0, sticky="w")
        ttk.Button(durations, text="Change timers", command=self.edit_selected_durations).grid(row=0, column=1, padx=(12, 0))

        body = ttk.Frame(page, padding=(20, 0, 20, 8))
        body.grid(row=3, column=0, sticky="nsew")
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(body, text="Notes").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="Subtasks, one per line").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.details_notes = tk.Text(
            body,
            wrap="word",
            background=self.theme["note_paper"],
            foreground=self.theme["note_text"],
            insertbackground=self.theme["note_text"],
            relief="flat",
            padx=14,
            pady=14,
        )
        self.details_notes.grid(row=1, column=0, sticky="nsew", pady=(5, 0))
        self.details_subtasks = tk.Text(body, wrap="word", height=10)
        self.details_subtasks.grid(row=1, column=1, sticky="nsew", padx=(12, 0), pady=(5, 0))
        actions = ttk.Frame(page, padding=(20, 4, 20, 18))
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="Save task details", command=self.save_details).grid(row=0, column=0)
        self.details_status = ttk.Label(actions, text="", style="Muted.TLabel")
        self.details_status.grid(row=0, column=1, padx=(12, 0))

    def load_details(self) -> None:
        task = self.db.get_task(self.selected_task_id) if self.selected_task_id else None
        self.details_notes.delete("1.0", "end")
        self.details_subtasks.delete("1.0", "end")
        if not task:
            self.details_heading.configure(text="Select a task from List")
            self.details_title_var.set("")
            self.details_duration_label.configure(text="")
            return
        self.details_heading.configure(text=f"{task['priority']} task")
        self.details_title_var.set(task["title"])
        self.details_notes.insert("1.0", task.get("notes", ""))
        self.details_subtasks.insert(
            "1.0",
            "\n".join(item["text"] for item in self.db.get_subtasks(int(task["id"]))),
        )
        self.details_duration_label.configure(
            text=f"Work {task.get('task_work_minutes', 25)} min · Break {task.get('task_break_minutes', 5)} min"
        )
        self.details_status.configure(text="")

    def save_details(self) -> None:
        if self.selected_task_id is None:
            return
        title = " ".join(self.details_title_var.get().split())
        if not title:
            messagebox.showerror(APP_NAME, "Task title cannot be empty.", parent=self)
            return
        self.db.update_task(
            self.selected_task_id,
            {"title": title, "notes": self.details_notes.get("1.0", "end-1c")},
        )
        self.db.replace_subtasks(
            self.selected_task_id,
            [line.strip() for line in self.details_subtasks.get("1.0", "end-1c").splitlines() if line.strip()],
        )
        self.details_status.configure(text="Saved")
        self.render_tasks([self.selected_task_id])
        self.refresh_history()

    # ---------- Task timer ----------

    def _load_timer_state(self) -> None:
        state = self.db.get_setting("task_timer_state", {})
        if not isinstance(state, dict):
            return
        self.timer_mode = "break" if state.get("mode") in ("break", "rest") else "work"
        self.timer_task_id = int(state["task_id"]) if state.get("task_id") not in (None, "") else None
        self.selected_task_id = self.timer_task_id
        default = self._task_duration_seconds(self.timer_task_id, self.timer_mode)
        self.timer_remaining = max(0, int(state.get("remaining", default) or default))
        self.timer_running = bool(state.get("running", False))
        target = state.get("target_end")
        started = state.get("started_at")
        try:
            self.timer_target_end = datetime.fromisoformat(target) if target else None
            self.timer_started_at = datetime.fromisoformat(started) if started else None
        except ValueError:
            self.timer_target_end = None
            self.timer_started_at = None
            self.timer_running = False
        if self.timer_running and self.timer_target_end:
            self.timer_remaining = max(0, math.ceil((self.timer_target_end - datetime.now().astimezone()).total_seconds()))

    def _save_timer_state(self) -> None:
        self.db.set_setting(
            "task_timer_state",
            {
                "mode": self.timer_mode,
                "task_id": self.timer_task_id,
                "remaining": self.timer_remaining,
                "running": self.timer_running,
                "target_end": self.timer_target_end.isoformat(timespec="seconds") if self.timer_target_end else None,
                "started_at": self.timer_started_at.isoformat(timespec="seconds") if self.timer_started_at else None,
            },
        )

    def _task_duration_minutes(self, task_id: int | None, mode: str) -> int:
        if task_id is not None:
            task = self.db.get_task(task_id)
            if task:
                key = "task_work_minutes" if mode == "work" else "task_break_minutes"
                return max(1, int(task.get(key, 25 if mode == "work" else 5) or 1))
        return max(1, int(self.default_work_minutes.get() if mode == "work" else self.default_break_minutes.get()))

    def _task_duration_seconds(self, task_id: int | None, mode: str) -> int:
        return self._task_duration_minutes(task_id, mode) * 60

    def start_timer_mode(self, mode: str) -> None:
        if mode not in ("work", "break"):
            return
        requested_task_id = self.selected_task_id if self.selected_task_id is not None else self.timer_task_id
        if self.timer_running:
            if self.timer_mode == mode and requested_task_id == self.timer_task_id:
                self.pause_timer()
                return
            self.pause_timer(record_incomplete=self.timer_mode == "work")
        full_seconds = self._task_duration_seconds(requested_task_id, mode)
        can_resume = (
            self.timer_mode == mode
            and self.timer_task_id == requested_task_id
            and 0 < self.timer_remaining < full_seconds
        )
        self.timer_task_id = requested_task_id
        self.timer_mode = mode
        now = datetime.now().astimezone()
        if can_resume:
            elapsed = full_seconds - self.timer_remaining
            self.timer_started_at = now - timedelta(seconds=elapsed)
        else:
            self.timer_remaining = full_seconds
            self.timer_started_at = now
        self.timer_running = True
        self.timer_target_end = now + timedelta(seconds=self.timer_remaining)
        self._save_timer_state()
        self._update_timer_labels()
        self._timer_tick()

    def pause_timer(self, record_incomplete: bool = False) -> None:
        if self.timer_running and self.timer_target_end:
            self.timer_remaining = max(0, math.ceil((self.timer_target_end - datetime.now().astimezone()).total_seconds()))
        if record_incomplete and self.timer_mode == "work" and self.timer_started_at:
            now = datetime.now().astimezone()
            actual = max(0, int((now - self.timer_started_at).total_seconds()))
            if actual > 0:
                self.db.add_pomodoro_session(
                    self.timer_task_id,
                    self.timer_started_at.isoformat(timespec="seconds"),
                    now.isoformat(timespec="seconds"),
                    self._task_duration_seconds(self.timer_task_id, "work"),
                    actual,
                    False,
                )
        self.timer_running = False
        self.timer_target_end = None
        if self.timer_job:
            try:
                self.after_cancel(self.timer_job)
            except tk.TclError:
                pass
            self.timer_job = None
        self._save_timer_state()
        self._update_timer_labels()

    def reset_timer(self) -> None:
        self.pause_timer()
        self.timer_remaining = self._task_duration_seconds(self.timer_task_id, self.timer_mode)
        self.timer_started_at = None
        self._save_timer_state()
        self._update_timer_labels()

    def _timer_tick(self) -> None:
        if not self.timer_running or self.timer_target_end is None:
            return
        self.timer_remaining = max(0, math.ceil((self.timer_target_end - datetime.now().astimezone()).total_seconds()))
        self._update_timer_labels()
        if self.timer_remaining <= 0:
            self.timer_running = False
            self.timer_target_end = None
            self.timer_job = None
            self._timer_complete()
            return
        self.timer_job = self.after(200, self._timer_tick)

    def _timer_complete(self) -> None:
        self.bell()
        now = datetime.now().astimezone()
        if self.timer_mode == "work":
            planned = self._task_duration_seconds(self.timer_task_id, "work")
            started = self.timer_started_at or now - timedelta(seconds=planned)
            self.db.add_pomodoro_session(
                self.timer_task_id,
                started.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                planned,
                planned,
                True,
            )
            if self.lock_enabled.get():
                self.after(100, self._lock_after_work)
            self.timer_mode = "break"
        else:
            self.timer_mode = "work"
        self.timer_remaining = self._task_duration_seconds(self.timer_task_id, self.timer_mode)
        self.timer_started_at = None
        self._save_timer_state()
        self._update_timer_labels()
        self.refresh_all()
        if self.auto_start.get():
            self.start_timer_mode(self.timer_mode)

    def _lock_after_work(self) -> None:
        ok, detail = lock_screen()
        if not ok:
            messagebox.showerror(APP_NAME, "Work finished, but screen locking failed.\n\n" + detail, parent=self)

    def _update_timer_labels(self) -> None:
        if not hasattr(self, "timer_label"):
            return
        minutes, seconds = divmod(max(0, self.timer_remaining), 60)
        self.timer_label.configure(text=f"{minutes:02d}:{seconds:02d}")
        task_id = self.timer_task_id if self.timer_running or self.timer_task_id else self.selected_task_id
        work = self._task_duration_minutes(task_id, "work")
        break_minutes = self._task_duration_minutes(task_id, "break")
        self.work_duration_label.configure(text=f"Work {work}m")
        self.break_duration_label.configure(text=f"Break {break_minutes}m")
        task = self.db.get_task(task_id) if task_id else None
        mode = "WORK" if self.timer_mode == "work" else "BREAK"
        self.timer_task_label.configure(text=f"{mode} · {task['title'] if task else 'No task selected'}")

    def edit_selected_durations(self) -> None:
        task_id = self.selected_task_id or self.timer_task_id
        if task_id is not None:
            task = self.db.get_task(task_id)
            if not task:
                return
            dialog = DurationDialog(
                self,
                "Task timer durations",
                int(task.get("task_work_minutes", 25)),
                int(task.get("task_break_minutes", 5)),
            )
            self.wait_window(dialog)
            if not dialog.result:
                return
            self.db.update_task(task_id, {"task_work_minutes": dialog.result[0], "task_break_minutes": dialog.result[1]})
        else:
            dialog = DurationDialog(
                self,
                "Default timer durations",
                int(self.default_work_minutes.get()),
                int(self.default_break_minutes.get()),
            )
            self.wait_window(dialog)
            if not dialog.result:
                return
            self.default_work_minutes.set(dialog.result[0])
            self.default_break_minutes.set(dialog.result[1])
            self.save_settings()
        if not self.timer_running:
            self.timer_remaining = self._task_duration_seconds(self.timer_task_id, self.timer_mode)
        self._save_timer_state()
        self.refresh_all()

    # ---------- Project folders ----------

    def _build_projects_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        header = ttk.Frame(page, padding=(18, 16, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self.project_page_title = tk.Label(
            header,
            text="Organize your tasks into projects",
            background=self.theme["background"],
            foreground="#ffffff",
            font=("TkDefaultFont", 9),
            anchor="w",
        )
        self.project_page_title.grid(row=0, column=0, sticky="w")
        self.project_new_folder_button = ttk.Button(header, text="New folder", command=self.new_project_folder)
        self.project_new_folder_button.grid(row=0, column=1, sticky="e")

        # Folder browser: only coloured folder rectangles are shown here.
        self.project_folder_browser = tk.Frame(page, background=self.theme["background"], padx=18, pady=8)
        self.project_folder_browser.grid(row=1, column=0, sticky="nsew")
        self.project_folder_browser.columnconfigure(0, weight=1)
        self.project_folder_browser.rowconfigure(0, weight=1)
        self.project_folder_canvas = tk.Canvas(
            self.project_folder_browser, background=self.theme["background"], borderwidth=0, highlightthickness=0
        )
        folder_scroll = ttk.Scrollbar(
            self.project_folder_browser, orient="vertical", command=self.project_folder_canvas.yview
        )
        self.project_folder_canvas.configure(yscrollcommand=folder_scroll.set)
        self.project_folder_canvas.grid(row=0, column=0, sticky="nsew")
        folder_scroll.grid(row=0, column=1, sticky="ns")
        self.project_folder_grid = tk.Frame(self.project_folder_canvas, background=self.theme["background"])
        self.project_folder_window = self.project_folder_canvas.create_window(
            (0, 0), window=self.project_folder_grid, anchor="nw"
        )
        self.project_folder_grid.bind(
            "<Configure>",
            lambda _e: self.project_folder_canvas.configure(scrollregion=self.project_folder_canvas.bbox("all")),
        )
        self.project_folder_canvas.bind(
            "<Configure>",
            lambda e: self.project_folder_canvas.itemconfigure(self.project_folder_window, width=e.width),
        )

        # Open-folder view. It replaces the folder browser instead of appearing under it.
        self.project_detail = tk.Frame(page, background=self.theme["background"], padx=18, pady=8)
        self.project_detail.columnconfigure(0, weight=1)
        self.project_detail.rowconfigure(2, weight=1)

        detail_header = tk.Frame(self.project_detail, background=self.theme["background"])
        detail_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        detail_header.columnconfigure(1, weight=1)
        tk.Button(
            detail_header, text="← Folders", command=self.close_project_folder,
            background=self.theme["panel"], foreground=self.theme["text"],
            activebackground=self.theme["hover"], activeforeground=self.theme["text"],
            relief="flat", borderwidth=0, highlightthickness=0, padx=7, pady=3, cursor="hand2",
        ).grid(row=0, column=0, padx=(0, 8))
        self.project_selected_label = tk.Label(
            detail_header, text="", background=self.theme["background"],
            foreground=self.theme["text"], font=("TkDefaultFont", 14, "bold"), anchor="w",
        )
        self.project_selected_label.grid(row=0, column=1, sticky="w")
        ttk.Button(detail_header, text="Add task", command=self.add_task_to_selected_folder).grid(row=0, column=2, padx=3)
        ttk.Button(detail_header, text="Edit folder", command=self.edit_selected_project_folder).grid(row=0, column=3, padx=3)
        ttk.Button(detail_header, text="Delete folder", command=self.delete_selected_project_folder).grid(row=0, column=4, padx=3)

        self.project_folder_hint = tk.Label(
            self.project_detail,
            text="Folder tasks can stay unscheduled or also appear on any calendar day.",
            background=self.theme["background"], foreground=self.theme["muted"], anchor="w",
            font=("TkDefaultFont", 9),
        )
        self.project_folder_hint.grid(row=1, column=0, sticky="w", pady=(0, 8))

        list_shell = tk.Frame(self.project_detail, background=self.theme["background"])
        list_shell.grid(row=2, column=0, sticky="nsew")
        list_shell.columnconfigure(0, weight=1)
        list_shell.rowconfigure(0, weight=1)
        self.project_task_canvas = tk.Canvas(
            list_shell, background=self.theme["background"], borderwidth=0, highlightthickness=0, relief="flat"
        )
        project_scroll = ttk.Scrollbar(list_shell, orient="vertical", command=self.project_task_canvas.yview)
        self.project_task_canvas.configure(yscrollcommand=project_scroll.set)
        self.project_task_canvas.grid(row=0, column=0, sticky="nsew")
        project_scroll.grid(row=0, column=1, sticky="ns")
        self.project_task_inner = tk.Frame(self.project_task_canvas, background=self.theme["background"])
        self.project_task_window = self.project_task_canvas.create_window(
            (0, 0), window=self.project_task_inner, anchor="nw"
        )
        self.project_task_inner.bind(
            "<Configure>", lambda _e: self.project_task_canvas.configure(scrollregion=self.project_task_canvas.bbox("all"))
        )
        self.project_task_canvas.bind(
            "<Configure>", lambda e: self.project_task_canvas.itemconfigure(self.project_task_window, width=e.width)
        )

    def refresh_project_folders(self) -> None:
        if not hasattr(self, "project_folder_grid"):
            return
        for child in self.project_folder_grid.winfo_children():
            child.destroy()
        folders = self.db.get_project_folders()
        valid = {int(item["id"]) for item in folders}
        if self.selected_project_folder_id not in valid:
            self.selected_project_folder_id = None

        if self.selected_project_folder_id is None:
            self.project_detail.grid_remove()
            self.project_folder_browser.grid(row=1, column=0, sticky="nsew")
            self.project_page_title.configure(text="Organize your tasks into projects")
            self.project_new_folder_button.grid()
            if not folders:
                tk.Label(
                    self.project_folder_grid, text="No project folders yet.",
                    background=self.theme["background"], foreground=self.theme["muted"],
                    anchor="w", pady=14,
                ).grid(row=0, column=0, sticky="w")
                return
            columns = 4
            for column in range(columns):
                self.project_folder_grid.columnconfigure(column, weight=1, uniform="folders")
            for index, folder in enumerate(folders):
                folder_id = int(folder["id"])
                color = str(folder.get("color") or "#526d82")
                button = tk.Button(
                    self.project_folder_grid,
                    text=str(folder["name"]),
                    command=lambda item=folder_id: self.select_project_folder(item),
                    background=color, foreground="#ffffff",
                    activebackground=color, activeforeground="#ffffff",
                    font=("TkDefaultFont", 12, "bold"),
                    relief="flat", borderwidth=0, highlightthickness=0,
                    width=21, height=5, wraplength=165, justify="center", cursor="hand2",
                )
                row, column = divmod(index, columns)
                button.grid(row=row, column=column, sticky="nsew", padx=8, pady=8)
            return

        self.project_folder_browser.grid_remove()
        self.project_detail.grid(row=1, column=0, sticky="nsew")
        self.project_new_folder_button.grid_remove()
        self.refresh_project_folder_tasks()

    def select_project_folder(self, folder_id: int) -> None:
        self.selected_project_folder_id = int(folder_id)
        self.refresh_project_folders()

    def close_project_folder(self) -> None:
        self.selected_project_folder_id = None
        self.refresh_project_folders()

    def new_project_folder(self) -> None:
        dialog = ProjectFolderDialog(self)
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            self.db.create_project_folder(*dialog.result)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.selected_project_folder_id = None
        self.refresh_project_folders()

    def edit_selected_project_folder(self) -> None:
        if self.selected_project_folder_id is None:
            return
        folder = self.db.get_project_folder(self.selected_project_folder_id)
        if not folder:
            return
        dialog = ProjectFolderDialog(self, folder)
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            self.db.update_project_folder(self.selected_project_folder_id, *dialog.result)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.refresh_project_folders()

    def delete_selected_project_folder(self) -> None:
        if self.selected_project_folder_id is None:
            return
        folder = self.db.get_project_folder(self.selected_project_folder_id)
        if not folder:
            return
        if not messagebox.askyesno(
            "Delete folder",
            f"Delete project folder '{folder['name']}'? Tasks are kept and only lose the folder assignment.",
            parent=self,
        ):
            return
        self.db.delete_project_folder(self.selected_project_folder_id)
        self.selected_project_folder_id = None
        self.refresh_all()

    def add_task_to_selected_folder(self) -> None:
        if self.selected_project_folder_id is None:
            return
        dialog = TaskDialog(self, default_folder_id=self.selected_project_folder_id)
        self.wait_window(dialog)
        if not dialog.result:
            return
        task_id = self.db.create_task(dialog.result["values"])
        self.db.set_exceptions(task_id, dialog.result["exceptions"])
        self.db.replace_subtasks(task_id, dialog.result["subtasks"])
        self.refresh_all()

    def refresh_project_folder_tasks(self) -> None:
        if not hasattr(self, "project_task_inner"):
            return
        for child in self.project_task_inner.winfo_children():
            child.destroy()
        folder_id = self.selected_project_folder_id
        folder = self.db.get_project_folder(folder_id) if folder_id is not None else None
        if not folder:
            return
        self.project_selected_label.configure(text=str(folder["name"]))
        tasks = self.db.tasks_for_folder(folder_id)

        for column, priority in enumerate(("P1", "P2", "P3", "P4")):
            self.project_task_inner.columnconfigure(column, weight=1, uniform="project_priority")
            lane = tk.Frame(
                self.project_task_inner, background=self.theme["background"], borderwidth=0,
                highlightthickness=0, padx=4,
            )
            lane.grid(row=0, column=column, sticky="nsew")
            tk.Label(
                lane, text=PRIORITY_LABELS[priority], background=self.theme["background"],
                foreground=self.theme[priority], font=("TkDefaultFont", 10, "bold"), anchor="w",
            ).pack(fill="x", pady=(0, 8))
            lane_tasks = [task for task in tasks if str(task.get("priority", "P4")) == priority]
            if not lane_tasks:
                tk.Label(
                    lane, text="No tasks", background=self.theme["background"],
                    foreground=self.theme["muted"], anchor="w", font=("TkDefaultFont", 9),
                ).pack(fill="x", pady=(4, 0))
                continue
            for task in lane_tasks:
                self._build_project_task_card(task, lane)

    def _build_project_task_card(self, task: Mapping[str, Any], parent: tk.Widget) -> None:
        task_id = int(task["id"])
        priority = str(task.get("priority", "P4"))
        background = self.theme.get(priority, self.theme["field"])
        foreground = contrast_text(background)
        done = bool(task.get("status") == "completed")
        card = tk.Frame(parent, background=background, borderwidth=0, highlightthickness=0)
        card.pack(fill="x", pady=(0, 5))
        row = tk.Frame(card, background=background, padx=4, pady=2)
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)
        tk.Button(
            row, text="●" if done else "○", command=lambda item=task_id: self.toggle_task_done(item),
            background=background, foreground=foreground, activebackground=background, activeforeground=foreground,
            relief="flat", borderwidth=0, highlightthickness=0, font=("TkDefaultFont", 14, "bold"),
            padx=2, pady=0, cursor="hand2",
        ).grid(row=0, column=0, sticky="n")
        title_font = tkfont.Font(font=("TkDefaultFont", 9, "bold"))
        title_font.configure(overstrike=done)
        self.task_fonts.append(title_font)
        title = tk.Label(
            row, text=" ".join(str(task["title"]).split()), background=background, foreground=foreground,
            font=title_font, anchor="w", justify="left", wraplength=200, cursor="xterm", padx=2, pady=1,
        )
        title.grid(row=0, column=1, sticky="ew")
        title.bind("<Double-1>", lambda _event, item=task_id: self.edit_task(item))
        schedule = (
            f"Recurring · {task.get('recurrence_start') or 'unscheduled'}"
            if task.get("recurrence_enabled")
            else (task.get("deadline") or "Unscheduled")
        )
        tk.Label(
            row, text=schedule, background=background, foreground=foreground,
            font=("TkDefaultFont", 8), anchor="w",
        ).grid(row=1, column=1, sticky="w")
        tools = tk.Frame(card, background=background)
        tools.pack(fill="x", padx=4, pady=(0, 2))
        for text, command in (
            ("Today", lambda item=task_id: self.schedule_folder_task_today(item)),
            ("Move / date", lambda item=task_id: self.move_task_dialog(item)),
            ("Edit", lambda item=task_id: self.edit_task(item)),
            ("Remove folder", lambda item=task_id: self.remove_task_from_folder(item)),
        ):
            tk.Button(
                tools, text=text, command=command, background=background, foreground=foreground,
                activebackground=background, activeforeground=foreground, relief="flat", borderwidth=0,
                highlightthickness=0, padx=3, pady=0, font=("TkDefaultFont", 8), cursor="hand2",
            ).pack(side="left", padx=(0, 2))

    def schedule_folder_task_today(self, task_id: int) -> None:
        task = self.db.get_task(task_id)
        if not task:
            return
        if task.get("recurrence_enabled"):
            self.db.update_task(task_id, {"recurrence_start": date.today().isoformat()})
        else:
            self.db.update_task(task_id, {"deadline": date.today().isoformat()})
        self.refresh_all()

    def remove_task_from_folder(self, task_id: int) -> None:
        self.db.assign_task_folder(task_id, None)
        self.refresh_all()

    # ---------- Clock in/out ----------

    def _load_clock_state(self) -> dict[str, Any]:
        state = self.db.get_setting("clock_state", {})
        if not isinstance(state, dict):
            state = {}
        return {
            "clocked_in": bool(state.get("clocked_in", False)),
            "start_at": state.get("start_at"),
        }

    def _save_clock_state(self) -> None:
        self.db.set_setting("clock_state", self.clock_state)

    def toggle_clock(self) -> None:
        if self.clock_state.get("clocked_in"):
            self.clock_out()
        else:
            self.clock_in()

    def clock_in(self) -> None:
        self.clock_state = {
            "clocked_in": True,
            "start_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self._save_clock_state()
        self._refresh_clock_button()
        self.refresh_calendar()

    def clock_out(self) -> None:
        start_text = self.clock_state.get("start_at")
        try:
            start = datetime.fromisoformat(start_text) if start_text else None
        except ValueError:
            start = None
        end = datetime.now().astimezone()
        if start and end > start:
            cursor = start
            while cursor.date() < end.date():
                boundary = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time(), tzinfo=cursor.tzinfo)
                seconds = max(0, int((boundary - cursor).total_seconds()))
                self.db.add_work_session(cursor.date().isoformat(), cursor.isoformat(timespec="seconds"), boundary.isoformat(timespec="seconds"), seconds)
                cursor = boundary
            seconds = max(0, int((end - cursor).total_seconds()))
            if seconds:
                self.db.add_work_session(cursor.date().isoformat(), cursor.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), seconds)
        self.clock_state = {"clocked_in": False, "start_at": None}
        self._save_clock_state()
        self._refresh_clock_button()
        self.refresh_all()

    def _live_clock_seconds_today(self) -> int:
        if not self.clock_state.get("clocked_in"):
            return 0
        start_text = self.clock_state.get("start_at")
        try:
            start = datetime.fromisoformat(start_text) if start_text else None
        except ValueError:
            return 0
        now = datetime.now().astimezone()
        if not start:
            return 0
        today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=now.tzinfo)
        effective = max(start, today_start)
        return max(0, int((now - effective).total_seconds()))

    def _refresh_clock_button(self) -> None:
        if not hasattr(self, "clock_button"):
            return
        if self.clock_state.get("clocked_in"):
            self.clock_button.configure(
                text="Clock out", background="#4ade80", activebackground="#22c55e", foreground="#10351f"
            )
        else:
            self.clock_button.configure(
                text="Clock in", background="#86efac", activebackground="#6ee7a0", foreground="#10351f"
            )

    # ---------- Calendar ----------

    def _build_calendar_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(page, padding=(18, 14, 18, 8))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(3, weight=1)
        ttk.Button(toolbar, text="‹", command=lambda: self.change_month(-1)).grid(row=0, column=0, padx=3)
        ttk.Button(toolbar, text="Today", command=self.calendar_today).grid(row=0, column=1, padx=3)
        ttk.Button(toolbar, text="Adjust clocked total", command=self.adjust_selected_day_total).grid(row=0, column=2, padx=3)
        self.calendar_title = ttk.Label(toolbar, text="", font=("TkDefaultFont", 16, "bold"), anchor="center")
        self.calendar_title.grid(row=0, column=3, sticky="ew")
        ttk.Button(toolbar, text="›", command=lambda: self.change_month(1)).grid(row=0, column=4, padx=3)

        body = ttk.Frame(page, padding=(18, 0, 18, 18))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)
        self.calendar_grid = tk.Frame(body, background=self.theme["background"])
        self.calendar_grid.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        detail = ttk.LabelFrame(body, text="Selected day", padding=12)
        detail.grid(row=0, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(4, weight=1)
        self.calendar_selected_label = ttk.Label(detail, text="", font=("TkDefaultFont", 13, "bold"))
        self.calendar_selected_label.grid(row=0, column=0, sticky="w")
        self.calendar_work_label = ttk.Label(detail, text="")
        self.calendar_work_label.grid(row=1, column=0, sticky="w", pady=(8, 2))
        self.calendar_pomo_label = ttk.Label(detail, text="")
        self.calendar_pomo_label.grid(row=2, column=0, sticky="w", pady=2)
        ttk.Button(detail, text="Add task", command=self.add_task_to_selected_calendar_day).grid(row=3, column=0, sticky="w", pady=(8, 10))

        calendar_list_shell = tk.Frame(detail, background=self.theme["background"])
        calendar_list_shell.grid(row=4, column=0, sticky="nsew")
        calendar_list_shell.columnconfigure(0, weight=1)
        calendar_list_shell.rowconfigure(0, weight=1)
        self.calendar_task_canvas = tk.Canvas(
            calendar_list_shell, background=self.theme["field"], borderwidth=0, highlightthickness=0, relief="flat"
        )
        calendar_scroll = ttk.Scrollbar(calendar_list_shell, orient="vertical", command=self.calendar_task_canvas.yview)
        self.calendar_task_canvas.configure(yscrollcommand=calendar_scroll.set)
        self.calendar_task_canvas.grid(row=0, column=0, sticky="nsew")
        calendar_scroll.grid(row=0, column=1, sticky="ns")
        self.calendar_task_inner = tk.Frame(self.calendar_task_canvas, background=self.theme["field"])
        self.calendar_task_window = self.calendar_task_canvas.create_window((0, 0), window=self.calendar_task_inner, anchor="nw")
        self.calendar_task_inner.bind(
            "<Configure>", lambda _e: self.calendar_task_canvas.configure(scrollregion=self.calendar_task_canvas.bbox("all"))
        )
        self.calendar_task_canvas.bind(
            "<Configure>", lambda e: self.calendar_task_canvas.itemconfigure(self.calendar_task_window, width=e.width)
        )

    def refresh_calendar(self) -> None:
        if not hasattr(self, "calendar_grid"):
            return
        for child in self.calendar_grid.winfo_children():
            child.destroy()
        year, month = self.calendar_month.year, self.calendar_month.month
        self.calendar_title.configure(text=self.calendar_month.strftime("%B %Y"))
        last_day = calendar.monthrange(year, month)[1]
        start = date(year, month, 1)
        end = date(year, month, last_day)
        tasks_by_day = self.db.calendar_tasks_for_range(start, end)
        work = self.db.work_totals(start, end)
        if start <= date.today() <= end:
            work[date.today().isoformat()] = work.get(date.today().isoformat(), 0) + self._live_clock_seconds_today()
        pomodoro = self.db.pomodoro_totals(start, end)
        for column, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            self.calendar_grid.columnconfigure(column, weight=1)
            tk.Label(
                self.calendar_grid,
                text=name,
                background=self.theme["panel"],
                foreground=self.theme["text"],
                pady=5,
            ).grid(row=0, column=column, sticky="ew", padx=1, pady=1)
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
        for row_index, week in enumerate(weeks, start=1):
            self.calendar_grid.rowconfigure(row_index, weight=1)
            for column, day in enumerate(week):
                day_iso = day.isoformat()
                selected = day == self.selected_calendar_date
                in_month = day.month == month
                bg = self.theme["accent"] if selected else self.theme["field"]
                fg = self.theme["text"] if in_month else self.theme["muted"]
                cell = tk.Frame(
                    self.calendar_grid,
                    background=bg,
                    highlightthickness=1,
                    highlightbackground=self.theme["panel"],
                    cursor="hand2",
                )
                cell.grid(row=row_index, column=column, sticky="nsew", padx=1, pady=1)
                number = tk.Label(cell, text=str(day.day), background=bg, foreground=fg, font=("TkDefaultFont", 10, "bold"), anchor="w")
                number.pack(fill="x", padx=6, pady=(4, 0))
                totals = tk.Label(
                    cell,
                    text=f"W {format_duration(work.get(day_iso, 0))}\nP {format_duration(pomodoro.get(day_iso, 0))}",
                    justify="left",
                    background=bg,
                    foreground=fg,
                    anchor="w",
                )
                totals.pack(fill="x", padx=6, pady=(2, 0))
                day_tasks = tasks_by_day.get(day_iso, [])
                done_count = sum(1 for item in day_tasks if item.get("occurrence_completed"))
                count_label = tk.Label(
                    cell,
                    text=f"{done_count}/{len(day_tasks)} done" if day_tasks else "",
                    background=bg,
                    foreground=fg,
                    anchor="w",
                )
                count_label.pack(fill="x", padx=6, pady=(2, 5))

                def choose(_event: tk.Event | None = None, selected_day: date = day) -> None:
                    self.selected_calendar_date = selected_day
                    self.refresh_calendar()

                for widget in (cell, number, totals, count_label):
                    widget.bind("<Button-1>", choose)
        self._refresh_calendar_detail(tasks_by_day, work, pomodoro)

    def _refresh_calendar_detail(self, tasks_by_day, work, pomodoro) -> None:
        day_iso = self.selected_calendar_date.isoformat()
        self.calendar_selected_label.configure(text=self.selected_calendar_date.strftime("%A, %d %B %Y"))
        work_seconds = work.get(day_iso, self.db.work_seconds(day_iso))
        if self.selected_calendar_date == date.today():
            work_seconds = self.db.work_seconds(day_iso) + self._live_clock_seconds_today()
        self.calendar_work_label.configure(text=f"Clocked work: {format_duration(work_seconds)}")
        self.calendar_pomo_label.configure(text=f"Task timer work: {format_duration(pomodoro.get(day_iso, 0))}")
        tasks = tasks_by_day.get(day_iso)
        if tasks is None:
            tasks = self.db.calendar_tasks_for_range(self.selected_calendar_date, self.selected_calendar_date).get(day_iso, [])

        for child in self.calendar_task_inner.winfo_children():
            child.destroy()
        if not tasks:
            tk.Label(
                self.calendar_task_inner, text="No tasks scheduled", background=self.theme["field"],
                foreground=self.theme["muted"], anchor="w", padx=10, pady=10,
            ).pack(fill="x")
            return

        for task in tasks:
            task_id = int(task["id"])
            done = bool(task.get("occurrence_completed"))
            row = tk.Frame(self.calendar_task_inner, background=self.theme["field"], padx=8, pady=6)
            row.pack(fill="x", pady=(0, 1))
            row.columnconfigure(0, weight=1)
            font = tkfont.Font(font=("TkDefaultFont", 10))
            font.configure(overstrike=done)
            self.task_fonts.append(font)
            folder = self.db.get_project_folder(int(task["folder_id"])) if task.get("folder_id") else None
            label_text = str(task["title"])
            if folder:
                label_text += f"  ·  {folder['name']}"
            tk.Label(
                row, text=label_text, background=self.theme["field"],
                foreground=self.theme["muted"] if done else self.theme["text"],
                font=font, anchor="w", justify="left", wraplength=300,
            ).grid(row=0, column=0, sticky="ew")
            tk.Button(
                row, text="Folder", command=lambda item=task_id: self.assign_calendar_task_folder(item),
                background=self.theme["panel"], foreground=self.theme["text"],
                activebackground=self.theme["hover"], activeforeground=self.theme["text"],
                relief="flat", borderwidth=0, padx=5, pady=2, cursor="hand2",
            ).grid(row=0, column=1, padx=(6, 2))
            tk.Button(
                row, text="Edit", command=lambda item=task_id: self.edit_task(item),
                background=self.theme["panel"], foreground=self.theme["text"],
                activebackground=self.theme["hover"], activeforeground=self.theme["text"],
                relief="flat", borderwidth=0, padx=5, pady=2, cursor="hand2",
            ).grid(row=0, column=2, padx=(2, 0))

    def add_task_to_selected_calendar_day(self) -> None:
        dialog = TaskDialog(self, default_deadline=self.selected_calendar_date.isoformat())
        self.wait_window(dialog)
        if not dialog.result:
            return
        task_id = self.db.create_task(dialog.result["values"])
        self.db.set_exceptions(task_id, dialog.result["exceptions"])
        self.db.replace_subtasks(task_id, dialog.result["subtasks"])
        self.selected_task_id = task_id
        self.refresh_all()

    def assign_calendar_task_folder(self, task_id: int) -> None:
        task = self.db.get_task(task_id)
        if not task:
            return
        dialog = FolderPickerDialog(self, int(task.get("folder_id") or 0) or None)
        self.wait_window(dialog)
        if dialog.result is _NO_RESULT:
            return
        # Only folder_id changes here: the calendar date remains untouched.
        self.db.assign_task_folder(task_id, dialog.result)
        self.refresh_all()

    def change_month(self, amount: int) -> None:
        index = self.calendar_month.year * 12 + self.calendar_month.month - 1 + amount
        self.calendar_month = date(index // 12, index % 12 + 1, 1)
        self.refresh_calendar()

    def calendar_today(self) -> None:
        self.calendar_month = date.today().replace(day=1)
        self.selected_calendar_date = date.today()
        self.refresh_calendar()

    def adjust_selected_day_total(self) -> None:
        day = self.selected_calendar_date.isoformat()
        current = self.db.work_seconds(day) + (self._live_clock_seconds_today() if self.selected_calendar_date == date.today() else 0)
        TotalTimeDialog(self, f"Clocked total — {day}", current, lambda seconds: self._set_day_total(day, seconds))

    def _set_day_total(self, day: str, seconds: int) -> None:
        live = self._live_clock_seconds_today() if day == date.today().isoformat() else 0
        self.db.set_daily_total(day, max(0, seconds - live))
        self.refresh_all()

    # ---------- History ----------

    def _build_history_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        filters = ttk.Frame(page, padding=(18, 14, 18, 8))
        filters.grid(row=0, column=0, sticky="ew")
        filters.columnconfigure(0, weight=1)
        self.history_search_var = tk.StringVar()
        self.history_status_var = tk.StringVar(value="all")
        self.history_search_var.trace_add("write", lambda *_args: self.after_idle(self.refresh_history))
        self.history_status_var.trace_add("write", lambda *_args: self.after_idle(self.refresh_history))
        search = ttk.Entry(filters, textvariable=self.history_search_var)
        search.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        search.bind("<Return>", lambda _event: self.refresh_history())
        self.history_status_combo = ttk.Combobox(
            filters,
            textvariable=self.history_status_var,
            values=("all", "active", "completed", "archived"),
            state="readonly",
            width=12,
        )
        self.history_status_combo.grid(row=0, column=1, padx=4)
        self.history_status_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_history())
        ttk.Button(filters, text="Search", command=self.refresh_history).grid(row=0, column=2, padx=4)

        table_frame = ttk.Frame(page, padding=(18, 0, 18, 8))
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("date", "status", "priority", "task", "timers", "manual", "pomodoros")
        self.history_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "date": "Date",
            "status": "Status",
            "priority": "Priority",
            "task": "Task",
            "timers": "Work / break",
            "manual": "Manual time",
            "pomodoros": "Pomodoros",
        }
        widths = {"date": 125, "status": 95, "priority": 75, "task": 360, "timers": 110, "manual": 100, "pomodoros": 100}
        for column in columns:
            self.history_tree.heading(column, text=headings[column])
            self.history_tree.column(column, width=widths[column], anchor="w")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scrollbar.set)
        self.history_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.history_tree.bind("<Double-1>", lambda _event: self.edit_history_task())
        actions = ttk.Frame(page, padding=(18, 0, 18, 18))
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="Edit", command=self.edit_history_task).grid(row=0, column=0, padx=(0, 5))
        ttk.Button(actions, text="Restore / reopen", command=self.restore_history_task).grid(row=0, column=1, padx=5)
        ttk.Button(actions, text="Archive", command=self.archive_history_task).grid(row=0, column=2, padx=5)

    def refresh_history(self) -> None:
        if not hasattr(self, "history_tree"):
            return
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        self.history_records = {}
        records = self.db.search_history(
            search=self.history_search_var.get(),
            status=self.history_status_var.get(),
        )
        for index, record in enumerate(records):
            iid = f"r{index}"
            display_date = record.get("occurrence_date") or str(record.get("updated_at") or record.get("created_at") or "")[:10]
            status = record.get("status", "")
            timers = f"{record.get('task_work_minutes', 25)} / {record.get('task_break_minutes', 5)}"
            pomos = f"{record.get('pomodoro_completed', 0)}/{record.get('pomodoro_estimate', 0)}"
            self.history_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    display_date,
                    status,
                    record.get("priority", "P4"),
                    record.get("title", ""),
                    timers,
                    format_duration(int(record.get("manual_seconds", 0) or 0)),
                    pomos,
                ),
            )
            self.history_records[iid] = record

    def _selected_history_record(self) -> dict[str, Any] | None:
        selection = self.history_tree.selection()
        return self.history_records.get(selection[0]) if selection else None

    def edit_history_task(self) -> None:
        record = self._selected_history_record()
        if record:
            self.edit_task(int(record["id"]))

    def restore_history_task(self) -> None:
        record = self._selected_history_record()
        if not record:
            return
        task_id = int(record["id"])
        if record.get("record_type") == "occurrence" and record.get("occurrence_date"):
            self.db.reopen_task(task_id, date.fromisoformat(record["occurrence_date"]))
        else:
            self.db.restore_task(task_id)
        self.refresh_all()

    def archive_history_task(self) -> None:
        record = self._selected_history_record()
        if record:
            self.db.archive_task(int(record["id"]))
            self.refresh_all()

    # ---------- Graveyards ----------

    def _graveyard_handwriting_font(self) -> str:
        available = set(tkfont.families(self))
        for family in (
            "URW Chancery L",
            "Segoe Print",
            "Bradley Hand",
            "Comic Sans MS",
            "Purisa",
            "Chilanka",
        ):
            if family in available:
                return family
        return "TkDefaultFont"

    def _build_graveyards_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        paper = self.theme["note_paper"]
        ink = self.theme["graveyard_text"]

        notebook = tk.Frame(page, background=paper, borderwidth=0, highlightthickness=0)
        notebook.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        notebook.columnconfigure(0, weight=1)
        notebook.rowconfigure(1, weight=1)

        tk.Label(
            notebook,
            text="Graveyard of untimed tasks and ideas to do in the never-coming future.",
            background=paper,
            foreground="#a3a3a3",
            font=("TkDefaultFont", 9, "italic"),
            anchor="w",
            justify="left",
            padx=64,
            pady=10,
        ).grid(row=0, column=0, sticky="ew")

        body = tk.Frame(notebook, background=paper, borderwidth=0, highlightthickness=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.graveyard_canvas = tk.Canvas(
            body,
            background=paper,
            borderwidth=0,
            highlightthickness=0,
        )
        graveyard_scroll = ttk.Scrollbar(body, orient="vertical", command=self.graveyard_canvas.yview)
        self.graveyard_canvas.configure(yscrollcommand=graveyard_scroll.set)
        self.graveyard_canvas.grid(row=0, column=0, sticky="nsew")
        graveyard_scroll.grid(row=0, column=1, sticky="ns")

        self.graveyard_lines_frame = tk.Frame(
            self.graveyard_canvas,
            background=paper,
            borderwidth=0,
            highlightthickness=0,
        )
        self.graveyard_window = self.graveyard_canvas.create_window(
            (0, 0), window=self.graveyard_lines_frame, anchor="nw"
        )
        self.graveyard_lines_frame.bind(
            "<Configure>",
            lambda _event: self.graveyard_canvas.configure(
                scrollregion=self.graveyard_canvas.bbox("all")
            ),
        )
        self.graveyard_canvas.bind(
            "<Configure>",
            lambda event: self.graveyard_canvas.itemconfigure(
                self.graveyard_window, width=event.width
            ),
        )

        self.graveyard_entries: list[tk.Entry] = []
        self.graveyard_save_job: str | None = None
        self.graveyard_font_family = self._graveyard_handwriting_font()

        saved = str(self.db.get_setting("graveyard_notes", "") or "")
        saved_lines = saved.split("\n") if saved else []
        for value in saved_lines:
            self._append_graveyard_line(value)
        while len(self.graveyard_entries) < max(36, len(saved_lines) + 12):
            self._append_graveyard_line("")


    def _append_graveyard_line(self, value: str = "") -> tk.Entry:
        paper = self.theme["note_paper"]
        ink = self.theme["graveyard_text"]
        index = len(self.graveyard_entries)

        line = tk.Frame(
            self.graveyard_lines_frame,
            background=paper,
            borderwidth=0,
            highlightthickness=0,
        )
        line.pack(fill="x")
        line.columnconfigure(2, weight=1)

        tk.Frame(line, background=paper, width=52, height=30).grid(row=0, column=0, sticky="ns")
        tk.Frame(line, background="#cf6868", width=2).grid(row=0, column=1, sticky="ns")

        writing = tk.Frame(line, background=paper, borderwidth=0, highlightthickness=0)
        writing.grid(row=0, column=2, sticky="ew")
        writing.columnconfigure(0, weight=1)

        entry = tk.Entry(
            writing,
            background=paper,
            foreground=ink,
            insertbackground=ink,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=(self.graveyard_font_family, 13),
        )
        entry.grid(row=0, column=0, sticky="ew", padx=(10, 18), pady=(3, 1))
        entry.insert(0, value)

        tk.Frame(writing, background="#9bb7d2", height=1).grid(row=1, column=0, sticky="ew")

        entry.bind("<KeyRelease>", self._schedule_graveyard_save)
        entry.bind("<FocusOut>", lambda _event: self.save_graveyard_notes())
        entry.bind("<Return>", lambda event, item=index: self._graveyard_next_line(event, item))
        entry.bind("<Up>", lambda event, item=index: self._graveyard_move_line(event, item, -1))
        entry.bind("<Down>", lambda event, item=index: self._graveyard_move_line(event, item, 1))
        entry.bind("<<Paste>>", lambda event, item=index: self._graveyard_paste(event, item))

        self.graveyard_entries.append(entry)
        return entry

    def _schedule_graveyard_save(self, _event: tk.Event | None = None) -> None:
        if getattr(self, "graveyard_save_job", None):
            try:
                self.after_cancel(self.graveyard_save_job)
            except tk.TclError:
                pass
        self.graveyard_save_job = self.after(350, self.save_graveyard_notes)

    def save_graveyard_notes(self) -> None:
        if not hasattr(self, "graveyard_entries"):
            return
        self.graveyard_save_job = None
        lines = [entry.get().rstrip() for entry in self.graveyard_entries]
        while lines and not lines[-1]:
            lines.pop()
        self.db.set_setting("graveyard_notes", "\n".join(lines))

    def _graveyard_next_line(self, _event: tk.Event, index: int) -> str:
        if index + 1 >= len(self.graveyard_entries):
            self._append_graveyard_line("")
        target = self.graveyard_entries[index + 1]
        target.focus_set()
        target.icursor(tk.END)
        self._schedule_graveyard_save()
        return "break"

    def _graveyard_move_line(self, _event: tk.Event, index: int, direction: int) -> str | None:
        target_index = index + direction
        if not 0 <= target_index < len(self.graveyard_entries):
            return None
        source = self.graveyard_entries[index]
        target = self.graveyard_entries[target_index]
        column = source.index(tk.INSERT)
        target.focus_set()
        target.icursor(min(column, len(target.get())))
        return "break"

    def _graveyard_paste(self, _event: tk.Event, index: int) -> str | None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return None
        if "\n" not in text and "\r" not in text:
            return None

        chunks = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        entry = self.graveyard_entries[index]
        before = entry.get()[: entry.index(tk.INSERT)]
        after = entry.get()[entry.index(tk.INSERT) :]
        chunks[0] = before + chunks[0]
        chunks[-1] = chunks[-1] + after

        needed = index + len(chunks) - len(self.graveyard_entries)
        for _ in range(max(0, needed)):
            self._append_graveyard_line("")
        for offset, chunk in enumerate(chunks):
            target = self.graveyard_entries[index + offset]
            target.delete(0, tk.END)
            target.insert(0, chunk)
        target.focus_set()
        target.icursor(tk.END)
        self._schedule_graveyard_save()
        return "break"

    def _pointer_over_graveyard(self) -> bool:
        if self.current_page != "graveyards" or not hasattr(self, "graveyard_canvas"):
            return False
        try:
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            left = self.graveyard_canvas.winfo_rootx()
            top = self.graveyard_canvas.winfo_rooty()
            return (
                left <= x < left + self.graveyard_canvas.winfo_width()
                and top <= y < top + self.graveyard_canvas.winfo_height()
            )
        except tk.TclError:
            return False

    def _on_graveyard_mousewheel(self, event: tk.Event) -> str | None:
        if not self._pointer_over_graveyard():
            return None
        number = getattr(event, "num", None)
        if number == 4:
            steps = -1
        elif number == 5:
            steps = 1
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return None
            magnitude = max(1, abs(delta) // 120)
            steps = -magnitude if delta > 0 else magnitude
        self.graveyard_canvas.yview_scroll(steps, "units")
        return "break"

    # ---------- Settings and CSV ----------

    def _build_settings_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(page)
        notebook.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        general = ttk.Frame(notebook, padding=16)
        colors = ttk.Frame(notebook, padding=16)
        data = ttk.Frame(notebook, padding=16)
        notebook.add(general, text="General")
        notebook.add(colors, text="Colours")
        notebook.add(data, text="CSV")

        ttk.Label(general, text="Default work minutes").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Spinbox(general, from_=1, to=720, textvariable=self.default_work_minutes, width=8).grid(row=0, column=1, padx=(8, 20), pady=6)
        ttk.Label(general, text="Default break minutes").grid(row=0, column=2, sticky="w", pady=6)
        ttk.Spinbox(general, from_=1, to=720, textvariable=self.default_break_minutes, width=8).grid(row=0, column=3, padx=(8, 20), pady=6)
        ttk.Checkbutton(general, text="Auto-start next timer mode", variable=self.auto_start).grid(row=1, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Checkbutton(general, text="Lock Linux screen after completed work timer", variable=self.lock_enabled).grid(row=2, column=0, columnspan=4, sticky="w", pady=8)
        ttk.Button(general, text="Save settings", command=self.save_settings).grid(row=3, column=0, sticky="w", pady=(12, 0))

        color_keys = [
            ("background", "Background"),
            ("panel", "Panels"),
            ("accent", "Buttons / accent"),
            ("hover", "Button hover"),
            ("text", "Text"),
            ("muted", "Muted text"),
            ("field", "Lists / fields"),
            ("note_paper", "Notes paper"),
            ("note_text", "Task notes text"),
            ("graveyard_text", "Graveyards ink"),
            ("P1", "P1 Critical"),
            ("P2", "P2 High"),
            ("P3", "P3 Medium"),
            ("P4", "P4 Low"),
        ]
        self.color_buttons: dict[str, tk.Button] = {}
        for index, (key, label) in enumerate(color_keys):
            row, group = divmod(index, 2)
            column = group * 3
            ttk.Label(colors, text=label).grid(row=row, column=column, sticky="w", pady=5)
            button = tk.Button(
                colors,
                text=self.theme[key],
                width=14,
                background=self.theme[key],
                foreground=contrast_text(self.theme[key]),
                command=lambda selected=key: self.choose_color(selected),
            )
            button.grid(row=row, column=column + 1, sticky="w", padx=(8, 28), pady=5)
            self.color_buttons[key] = button
        opacity_row = (len(color_keys) + 1) // 2
        self.opacity_var = tk.DoubleVar(value=float(self.theme["opacity"]))
        ttk.Label(colors, text="Window opacity").grid(row=opacity_row, column=0, sticky="w", pady=(14, 5))
        ttk.Scale(colors, from_=0.25, to=1.0, variable=self.opacity_var, command=lambda _value: self.attributes("-alpha", self.opacity_var.get())).grid(
            row=opacity_row, column=1, columnspan=3, sticky="ew", pady=(14, 5)
        )
        ttk.Button(colors, text="Save colours", command=self.save_theme).grid(row=opacity_row + 1, column=0, pady=(12, 0))
        ttk.Button(colors, text="Reset colours", command=self.reset_theme).grid(row=opacity_row + 1, column=1, pady=(12, 0))

        ttk.Label(
            data,
            text="Upload task, clock-session, or task-timer CSV files. Exports remain separate.",
            wraplength=620,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
        ttk.Button(data, text="Upload CSV", command=self.import_csv).grid(row=1, column=0, padx=(0, 8))
        ttk.Button(data, text="Export tasks", command=self.export_tasks_csv).grid(row=1, column=1, padx=8)
        ttk.Button(data, text="Export clock records", command=self.export_work_csv).grid(row=1, column=2, padx=8)
        ttk.Button(data, text="Export task timers", command=self.export_pomodoro_csv).grid(row=2, column=1, padx=8, pady=8)

    def save_settings(self) -> None:
        try:
            work = max(1, min(720, int(self.default_work_minutes.get())))
            break_minutes = max(1, min(720, int(self.default_break_minutes.get())))
        except (ValueError, tk.TclError):
            return
        self.default_work_minutes.set(work)
        self.default_break_minutes.set(break_minutes)
        self.db.set_setting("work_minutes", work)
        self.db.set_setting("rest_minutes", break_minutes)
        self.db.set_setting("auto_start", bool(self.auto_start.get()))
        self.db.set_setting("lock_enabled", bool(self.lock_enabled.get()))
        if self.timer_task_id is None and not self.timer_running:
            self.timer_remaining = self._task_duration_seconds(None, self.timer_mode)
        self._save_timer_state()
        self._update_timer_labels()

    def choose_color(self, key: str) -> None:
        chosen = colorchooser.askcolor(color=self.theme[key], parent=self)[1]
        if not chosen:
            return
        self.theme[key] = chosen
        button = self.color_buttons[key]
        button.configure(text=chosen, background=chosen, foreground=contrast_text(chosen))

    def save_theme(self) -> None:
        self.theme["opacity"] = float(self.opacity_var.get())
        self.db.set_setting("theme", self.theme)
        self._rebuild_ui_for_theme()

    def reset_theme(self) -> None:
        self.theme = dict(DEFAULT_THEME)
        self.db.set_setting("theme", self.theme)
        self._rebuild_ui_for_theme()

    def _rebuild_ui_for_theme(self) -> None:
        self.save_graveyard_notes()
        self._apply_theme()
        for child in self.winfo_children():
            child.destroy()
        self.pages = {}
        self.nav_buttons = {}
        self._build_ui()
        self.refresh_all()
        self.show_page(self.current_page if self.current_page in self.pages else "tasks")

    def import_csv(self) -> None:
        filename = filedialog.askopenfilename(parent=self, filetypes=[("CSV files", "*.csv"), ("All files", "*")])
        if not filename:
            return
        imported = 0
        skipped = 0
        try:
            with open(filename, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                headers = [str(item or "").strip().lower() for item in (reader.fieldnames or [])]
                rows = list(reader)
            if not headers:
                raise ValueError("CSV has no header row.")
            if "start_at" in headers and "end_at" in headers:
                for row in rows:
                    try:
                        start_at = row.get("start_at") or ""
                        end_at = row.get("end_at") or ""
                        work_date = row.get("work_date") or start_at[:10]
                        seconds = int(row.get("worked_seconds") or 0)
                        self.db.add_work_session(work_date, start_at, end_at, seconds)
                        imported += 1
                    except (ValueError, TypeError):
                        skipped += 1
            elif "started_at" in headers and "ended_at" in headers:
                for row in rows:
                    try:
                        task_id = int(row["task_id"]) if row.get("task_id") else None
                        inserted = self.db.add_pomodoro_session(
                            task_id,
                            row.get("started_at") or "",
                            row.get("ended_at") or "",
                            int(row.get("planned_seconds") or 0),
                            int(row.get("actual_seconds") or 0),
                            str(row.get("completed", "1")).strip().lower() in ("1", "true", "yes"),
                        )
                        imported += int(inserted)
                        skipped += int(not inserted)
                    except (ValueError, TypeError):
                        skipped += 1
            else:
                title_header = next((name for name in ("title", "task", "name", "description") if name in headers), None)
                if not title_header:
                    title_header = headers[0]
                existing = {str(task["title"]).strip().lower() for task in self.db.all_tasks()}
                for row in rows:
                    normalized = {str(key or "").strip().lower(): value for key, value in row.items()}
                    title = " ".join(str(normalized.get(title_header, "")).split())
                    if not title or title.lower() in existing:
                        skipped += 1
                        continue
                    values = {
                        "title": title,
                        "notes": normalized.get("notes", ""),
                        "priority": normalized.get("priority", "P4") if normalized.get("priority", "P4") in PRIORITY_LABELS else "P4",
                        "task_work_minutes": int(normalized.get("task_work_minutes") or normalized.get("work_minutes") or self.default_work_minutes.get()),
                        "task_break_minutes": int(normalized.get("task_break_minutes") or normalized.get("break_minutes") or self.default_break_minutes.get()),
                        "deadline": normalized.get("deadline") or None,
                        "tracking_mode": normalized.get("tracking_mode", "both") if normalized.get("tracking_mode", "both") in TRACKING_LABELS else "both",
                    }
                    self.db.create_task(values)
                    existing.add(title.lower())
                    imported += 1
        except (OSError, csv.Error, ValueError) as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.refresh_all()
        messagebox.showinfo(APP_NAME, f"Imported {imported}. Skipped {skipped}.", parent=self)

    def _export_path(self, suggested: str) -> str:
        return filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            initialfile=suggested,
            filetypes=[("CSV files", "*.csv")],
        )

    def export_tasks_csv(self) -> None:
        filename = self._export_path("pimodoro_tasks.csv")
        if not filename:
            return
        rows = self.db.all_tasks()
        fields = [
            "id", "title", "notes", "priority", "status", "tracking_mode",
            "task_work_minutes", "task_break_minutes", "folder_id", "manual_seconds",
            "pomodoro_estimate", "pomodoro_completed", "deadline",
            "recurrence_enabled", "recurrence_kind", "recurrence_interval",
            "recurrence_weekdays", "recurrence_start", "recurrence_end", "recurrence_max",
            "created_at", "updated_at", "completed_at", "archived_at",
        ]
        with open(filename, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def export_work_csv(self) -> None:
        filename = self._export_path("pimodoro_clock_records.csv")
        if not filename:
            return
        rows = self.db.work_sessions()
        fields = ["id", "work_date", "start_at", "end_at", "worked_seconds", "created_at"]
        with open(filename, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def export_pomodoro_csv(self) -> None:
        filename = self._export_path("pimodoro_task_timers.csv")
        if not filename:
            return
        rows = self.db.all_pomodoros()
        fields = ["id", "task_id", "task_title", "occurrence_date", "started_at", "ended_at", "planned_seconds", "actual_seconds", "completed"]
        with open(filename, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    # ---------- Refresh and lifecycle ----------

    def refresh_all(self) -> None:
        if hasattr(self, "task_list_inner"):
            self.render_tasks()
        if hasattr(self, "project_folder_grid"):
            self.refresh_project_folders()
        if hasattr(self, "history_tree"):
            self.refresh_history()
        if hasattr(self, "calendar_grid"):
            self.refresh_calendar()
        self._update_timer_labels()
        self._refresh_clock_button()

    def _tick_header(self) -> None:
        now = datetime.now().astimezone()
        today_date = now.date()
        if today_date != self.last_housekeeping_date:
            self.db.archive_completed_before(today_date)
            self.last_housekeeping_date = today_date
            self.refresh_all()
        self.header_date.configure(text=now.strftime("%A, %d %B %Y"))
        self.header_time.configure(text=now.strftime("%H:%M:%S"))
        today = date.today().isoformat()
        clocked = self.db.work_seconds(today) + self._live_clock_seconds_today()
        pomo = self.db.pomodoro_totals(date.today(), date.today()).get(today, 0)
        self.header_totals.configure(text=f"Clocked {format_duration(clocked)}\nTask timer {format_duration(pomo)}")
        self.sidebar_work_total.configure(text=f"Today {format_duration(clocked)}")
        self.after(1000, self._tick_header)

    def close_app(self) -> None:
        self.save_graveyard_notes()
        self.save_open_accordions()
        self.pause_timer()
        self._save_clock_state()
        self.db.close()
        self.destroy()


if __name__ == "__main__":
    PiModoro().mainloop()
