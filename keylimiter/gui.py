"""Окно настройки правил на tkinter + сворачивание в трей."""

import queue
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from zoneinfo import available_timezones

from . import config as config_module
from .config import Config, Rule
from .keys import ALL_KEY_NAMES, vk_from_name
from .scheduler import DEFAULT_TIMEZONE, AutoEnterSettings, EnterTimer, EnterTimerGroup

MSK_LABEL = "MSK — Москва (UTC+03:00)"


def timezone_label(key: str) -> str:
    return MSK_LABEL if key == DEFAULT_TIMEZONE else key

COLUMNS = (
    ("key", "Клавиша", 110),
    ("limit", "Лимит", 70),
    ("window_ms", "Окно, мс", 90),
    ("block_ms", "Блокировка, мс", 120),
    ("enabled", "Вкл.", 60),
)
LOG_LIMIT = 200


def format_log_time(ts: float) -> str:
    """Время для журнала с миллисекундами: 14:03:27.415."""
    # Округляем один раз до целых мс: иначе .415 из-за float может стать .414,
    # а .9996 — «.1000» при неизменных секундах.
    seconds, millis = divmod(round(ts * 1000), 1000)
    return time.strftime("%H:%M:%S", time.localtime(seconds)) + f".{millis:03d}"


class RuleDialog(tk.Toplevel):
    """Диалог добавления/изменения правила с записью клавиши через хук."""

    def __init__(self, parent: tk.Misc, hook, rule: Rule | None = None):
        super().__init__(parent)
        self.title("Правило" if rule else "Новое правило")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._hook = hook
        self.result: Rule | None = None
        source = rule or Rule(key="ENTER")

        self.key_var = tk.StringVar(value=source.key)
        self.limit_var = tk.StringVar(value=str(source.limit))
        self.window_var = tk.StringVar(value=str(source.window_ms))
        self.block_var = tk.StringVar(value=str(source.block_ms))
        self.enabled_var = tk.BooleanVar(value=source.enabled)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(body, text="Клавиша:").grid(row=0, column=0, sticky="w", pady=4)
        combo = ttk.Combobox(body, textvariable=self.key_var, values=ALL_KEY_NAMES, width=18)
        combo.grid(row=0, column=1, sticky="we", pady=4)
        self.capture_btn = ttk.Button(body, text="Записать клавишу", command=self._capture)
        self.capture_btn.grid(row=0, column=2, padx=(8, 0), pady=4)

        for row, (label, var) in enumerate((
            ("Лимит нажатий:", self.limit_var),
            ("Окно, мс:", self.window_var),
            ("Блокировка, мс:", self.block_var),
        ), start=1):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=var, width=20).grid(row=row, column=1, sticky="we", pady=4)

        ttk.Checkbutton(body, text="Правило включено", variable=self.enabled_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 4))

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Отмена", command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())

    def _capture(self) -> None:
        if self._hook is None:
            messagebox.showinfo("Запись клавиши", "Хук не запущен.", parent=self)
            return
        self.capture_btn.config(text="Нажмите клавишу...", state="disabled")
        self._hook.capture_next_key()

    def on_captured(self, key_name: str) -> None:
        self.key_var.set(key_name)
        self.capture_btn.config(text="Записать клавишу", state="normal")

    def _ok(self) -> None:
        key = self.key_var.get().strip().upper()
        if vk_from_name(key) is None:
            messagebox.showerror("Ошибка", f"Неизвестная клавиша: {key}", parent=self)
            return
        values = {}
        for field, var, label in (
            ("limit", self.limit_var, "Лимит нажатий"),
            ("window_ms", self.window_var, "Окно"),
            ("block_ms", self.block_var, "Блокировка"),
        ):
            try:
                value = int(var.get().strip())
            except ValueError:
                messagebox.showerror("Ошибка", f"«{label}» должно быть целым числом.", parent=self)
                return
            if value < 1:
                messagebox.showerror("Ошибка", f"«{label}» должно быть больше нуля.", parent=self)
                return
            values[field] = value

        self.result = Rule(key=key, enabled=self.enabled_var.get(), **values)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        if self._hook is not None:
            self._hook.cancel_capture()
        self.grab_release()
        self.destroy()


class App:
    def __init__(self, cfg: Config, hook, press_enter=None) -> None:
        self.cfg = cfg
        self.hook = hook
        self._tray = None
        self._dialog: RuleDialog | None = None
        self._press_enter = press_enter
        self._timers = EnterTimerGroup(press_enter, cfg.timers)

        self.root = tk.Tk()
        self.root.title("KeyLimiter — ограничитель частоты нажатий")
        self.root.minsize(720, 720)

        self._build_ui()
        self._refresh_rules()
        self._refresh_timers()
        self._update_status()

        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.after(50, self._drain_events)

    # --- интерфейс ---

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x")
        self.toggle_btn = ttk.Button(top, text="Стоп", command=self._toggle)
        self.toggle_btn.pack(side="left")
        self.status_label = ttk.Label(top, text="")
        self.status_label.pack(side="left", padx=12)
        ttk.Button(top, text="Свернуть в трей", command=self.hide_to_tray).pack(side="right")
        ttk.Button(top, text="Сохранить", command=self._save).pack(side="right", padx=6)

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True, pady=(10, 0))
        rules_tab = ttk.Frame(notebook, padding=8)
        timers_tab = ttk.Frame(notebook, padding=8)
        notebook.add(rules_tab, text="Ограничитель клавиш")
        notebook.add(timers_tab, text="Таймеры Enter")

        self.tree = ttk.Treeview(rules_tab, columns=[c[0] for c in COLUMNS], show="headings", height=8)
        for name, title, width in COLUMNS:
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=(10, 6))
        self.tree.bind("<Double-1>", lambda _e: self._edit_rule())

        buttons = ttk.Frame(rules_tab)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Добавить", command=self._add_rule).pack(side="left")
        ttk.Button(buttons, text="Изменить", command=self._edit_rule).pack(side="left", padx=6)
        ttk.Button(buttons, text="Удалить", command=self._delete_rule).pack(side="left")

        self._build_auto_enter(timers_tab)
        notebook.select(timers_tab)

        ttk.Label(main, text="Журнал:").pack(anchor="w", pady=(10, 2))
        self.log = tk.Listbox(main, height=5)
        self.log.pack(fill="both", expand=True)

        hint = ("Пауза/возобновление — Ctrl+Alt+P. Закрытие окна сворачивает в трей; "
                "выход — из меню трея.")
        ttk.Label(main, text=hint, foreground="#666", wraplength=680).pack(anchor="w", pady=(6, 0))

    def _build_auto_enter(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Автоматический Enter", padding=10)
        panel.pack(fill="both", expand=True)
        rows = ttk.Frame(panel)
        rows.pack(fill="both", expand=True, pady=(0, 6))
        columns = (("id", "№", 40), ("time", "Время", 85), ("timezone", "Часовой пояс", 130),
                   ("count", "Нажатий", 65), ("interval", "Интервал, мс", 95),
                   ("status", "Состояние", 200))
        self.timer_tree = ttk.Treeview(rows, columns=[item[0] for item in columns],
                                      show="headings", height=5, selectmode="browse")
        for key, title, width in columns:
            self.timer_tree.heading(key, text=title)
            self.timer_tree.column(key, width=width, minwidth=30, anchor="center")
        self.timer_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(rows, orient="vertical", command=self.timer_tree.yview)
        scroll.pack(side="right", fill="y")
        self.timer_tree.config(yscrollcommand=scroll.set)
        self.timer_tree.bind("<<TreeviewSelect>>", self._on_timer_selected)

        timer_actions = ttk.Frame(panel)
        timer_actions.pack(fill="x", pady=(0, 10))
        self.auto_start_btn = ttk.Button(timer_actions, text="Запланировать",
                                         command=self._arm_auto_enter)
        self.auto_start_btn.pack(side="left")
        self.auto_cancel_btn = ttk.Button(timer_actions, text="Отменить",
                                          command=self._cancel_auto_enter)
        self.auto_cancel_btn.pack(side="left", padx=6)
        self.auto_start_all_btn = ttk.Button(timer_actions, text="Запланировать все",
                                             command=self._arm_all_auto_enter)
        self.auto_start_all_btn.pack(side="left", padx=6)
        self.auto_cancel_all_btn = ttk.Button(timer_actions, text="Отменить все",
                                              command=self._cancel_all_auto_enter)
        self.auto_cancel_all_btn.pack(side="left")
        ttk.Label(panel, text="Параметры нового / выбранного таймера:").pack(anchor="w", pady=(0, 6))
        hour, minute, second = self.cfg.auto_enter.time.split(":")
        self.auto_hour_var = tk.StringVar(value=hour)
        self.auto_minute_var = tk.StringVar(value=minute)
        self.auto_second_var = tk.StringVar(value=second)
        self.auto_timezone_var = tk.StringVar(value=timezone_label(self.cfg.auto_enter.timezone))
        self.auto_count_var = tk.StringVar(value=str(self.cfg.auto_enter.count))
        self.auto_interval_var = tk.StringVar(value=str(self.cfg.auto_enter.interval_ms))
        fields = ttk.Frame(panel)
        fields.pack(fill="x")
        self._auto_entries = []
        ttk.Label(fields, text="Время:").pack(side="left", padx=(0, 4))
        for label, var, maximum in (
            ("ч", self.auto_hour_var, 23),
            ("мин", self.auto_minute_var, 59),
            ("сек", self.auto_second_var, 59),
        ):
            entry = ttk.Spinbox(fields, textvariable=var,
                                values=[f"{value:02d}" for value in range(maximum + 1)],
                                width=3, wrap=True)
            entry.pack(side="left")
            ttk.Label(fields, text=label).pack(side="left", padx=(2, 6))
            self._auto_entries.append(entry)

        zone_fields = ttk.Frame(panel)
        zone_fields.pack(fill="x", pady=(8, 0))
        ttk.Label(zone_fields, text="Часовой пояс:").pack(side="left", padx=(0, 6))
        zones = [MSK_LABEL, "UTC"] + sorted(available_timezones() - {DEFAULT_TIMEZONE, "UTC"})
        self.auto_timezone_combo = ttk.Combobox(
            zone_fields, textvariable=self.auto_timezone_var, values=zones, width=34,
        )
        self.auto_timezone_combo.pack(side="left")

        count_fields = ttk.Frame(panel)
        count_fields.pack(fill="x", pady=(8, 0))
        for label, var, width in (
            ("Нажатий:", self.auto_count_var, 7),
            ("Интервал, мс:", self.auto_interval_var, 7),
        ):
            ttk.Label(count_fields, text=label).pack(side="left", padx=(0, 4))
            entry = ttk.Entry(count_fields, textvariable=var, width=width)
            entry.pack(side="left", padx=(0, 12))
            self._auto_entries.append(entry)

        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Добавить таймер", command=self._add_auto_timer).pack(side="left")
        self.auto_edit_btn = ttk.Button(actions, text="Применить к выбранному",
                                        command=self._edit_auto_timer)
        self.auto_edit_btn.pack(side="left", padx=6)
        self.auto_delete_btn = ttk.Button(actions, text="Удалить таймер", command=self._delete_auto_timer)
        self.auto_delete_btn.pack(side="left")
        ttk.Label(
            panel,
            text="Добавьте таймеры и запланируйте выбранный или все сразу. По умолчанию — MSK.\n"
                 "Enter идёт в активное окно. Стоп / Ctrl+Alt+P отменяет все таймеры.",
            foreground="#666", wraplength=620,
        ).pack(anchor="w", pady=(4, 0))

    def _read_auto_settings(self) -> AutoEnterSettings:
        parts = [self.auto_hour_var.get().strip(), self.auto_minute_var.get().strip(),
                 self.auto_second_var.get().strip()]
        if any(not part.isascii() or not part.isdigit() or len(part) > 2 for part in parts):
            raise ValueError("Часы, минуты и секунды должны быть целыми числами.")
        try:
            count = int(self.auto_count_var.get().strip())
            interval = int(self.auto_interval_var.get().strip())
        except ValueError:
            raise ValueError("Количество нажатий и интервал должны быть целыми числами.") from None
        zone = self.auto_timezone_var.get().strip()
        if zone == MSK_LABEL:
            zone = DEFAULT_TIMEZONE
        return AutoEnterSettings(time=":".join(part.zfill(2) for part in parts),
                                 count=count, interval_ms=interval, timezone=zone)

    def _show_auto_settings(self, settings: AutoEnterSettings) -> None:
        hour, minute, second = settings.time.split(":")
        self.auto_hour_var.set(hour)
        self.auto_minute_var.set(minute)
        self.auto_second_var.set(second)
        self.auto_timezone_var.set(timezone_label(settings.timezone))
        self.auto_count_var.set(str(settings.count))
        self.auto_interval_var.set(str(settings.interval_ms))

    def _selected_timer_id(self) -> int | None:
        selection = self.timer_tree.selection()
        return int(selection[0]) if selection else None

    def _on_timer_selected(self, _event=None) -> None:
        timer_id = self._selected_timer_id()
        if timer_id in self._timers.timers:
            self._show_auto_settings(self._timers.timers[timer_id].settings)
        self._set_auto_controls()

    @staticmethod
    def _timer_status(timer: EnterTimer) -> str:
        sent = timer.scheduler.sent
        statuses = {
            "idle": "Не запланирован",
            "running": f"Выполняется: {sent}/{timer.settings.count}",
            "completed": f"Готово: {sent} нажатий",
            "cancelled": f"Отменён: отправлено {sent}",
            "error": f"Ошибка: отправлено {sent}",
        }
        if timer.phase == "waiting":
            return f"Ожидание {timer.scheduler.target:%d.%m.%Y}"
        return statuses[timer.phase]

    def _refresh_timers(self) -> None:
        existing = set(self.timer_tree.get_children())
        for row in existing:
            if int(row) not in self._timers.timers:
                self.timer_tree.delete(row)
        for timer in self._timers.timers.values():
            zone = "MSK" if timer.settings.timezone == DEFAULT_TIMEZONE else timer.settings.timezone
            values = (timer.id, timer.settings.time, zone, timer.settings.count,
                      timer.settings.interval_ms, self._timer_status(timer))
            row = str(timer.id)
            if row in existing:
                self.timer_tree.item(row, values=values)
            else:
                self.timer_tree.insert("", "end", iid=row, values=values)
        self._set_auto_controls()

    def _set_auto_controls(self) -> None:
        timer = self._timers.timers.get(self._selected_timer_id())
        active = timer is not None and timer.scheduler.active
        can_arm = self._press_enter is not None and self.hook is not None and not self.hook.paused
        self.auto_start_btn.config(state="normal" if timer and not active and can_arm else "disabled")
        self.auto_cancel_btn.config(state="normal" if active else "disabled")
        self.auto_edit_btn.config(state="normal" if timer and not active else "disabled")
        self.auto_delete_btn.config(state="normal" if timer else "disabled")
        timers = self._timers.timers.values()
        has_idle = any(not item.scheduler.active for item in timers)
        has_active = any(item.scheduler.active for item in self._timers.timers.values())
        self.auto_start_all_btn.config(state="normal" if has_idle and can_arm else "disabled")
        self.auto_cancel_all_btn.config(state="normal" if has_active else "disabled")

    def _save_timers(self) -> None:
        self.cfg.timers = [timer.settings for timer in self._timers.timers.values()]
        self._save(quiet=True)

    def _add_auto_timer(self) -> None:
        try:
            settings = self._read_auto_settings()
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self.root)
            return
        timer_id = self._timers.add(settings)
        self.cfg.auto_enter = settings
        self._show_auto_settings(settings)
        self._save_timers()
        self._refresh_timers()
        self.timer_tree.selection_set(str(timer_id))
        self.timer_tree.see(str(timer_id))
        self._set_auto_controls()
        self._append_log(f"Добавлен таймер №{timer_id}")

    def _edit_auto_timer(self) -> None:
        timer_id = self._selected_timer_id()
        if timer_id is None:
            return
        try:
            settings = self._read_auto_settings()
            self._timers.edit(timer_id, settings)
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self.root)
            return
        self.cfg.auto_enter = settings
        self._show_auto_settings(settings)
        self._save_timers()
        self._refresh_timers()
        self._append_log(f"Изменён таймер №{timer_id}")

    def _delete_auto_timer(self) -> None:
        timer_id = self._selected_timer_id()
        if timer_id is None:
            return
        self._timers.remove(timer_id)
        self._save_timers()
        self._refresh_timers()
        self._append_log(f"Удалён таймер №{timer_id}; его оставшиеся нажатия отменены")

    def _can_arm_auto_enter(self) -> bool:
        if self._press_enter is None:
            messagebox.showerror("Автоматический Enter", "Отправка Enter недоступна.", parent=self.root)
            return False
        if self.hook is None or self.hook.paused:
            messagebox.showinfo("Автоматический Enter", "Сначала нажмите «Старт».", parent=self.root)
            return False
        return True

    def _plan_timer(self, timer_id: int, now: datetime) -> None:
        target = self._timers.arm(timer_id, now)
        settings = self._timers.timers[timer_id].settings
        zone = "MSK" if settings.timezone == DEFAULT_TIMEZONE else settings.timezone
        self._append_log(f"Таймер №{timer_id}: запуск {target:%d.%m.%Y %H:%M:%S} {zone}, "
                         f"{settings.count} нажатий")

    def _arm_auto_enter(self) -> None:
        timer_id = self._selected_timer_id()
        if timer_id is None or not self._can_arm_auto_enter():
            return
        try:
            self._plan_timer(timer_id, datetime.now(timezone.utc))
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self.root)
            return
        self._refresh_timers()

    def _arm_all_auto_enter(self) -> None:
        if not self._can_arm_auto_enter():
            return
        now = datetime.now(timezone.utc)
        for timer in self._timers.timers.values():
            if not timer.scheduler.active:
                self._plan_timer(timer.id, now)
        self._refresh_timers()

    def _log_timer_cancel(self, timer_id: int) -> None:
        sent = self._timers.timers[timer_id].scheduler.sent
        self._append_log(f"Таймер №{timer_id} отменён. Отправлено: {sent}")

    def _cancel_auto_enter(self) -> None:
        timer_id = self._selected_timer_id()
        if timer_id is not None and self._timers.cancel(timer_id):
            self._log_timer_cancel(timer_id)
            self._refresh_timers()

    def _cancel_all_auto_enter(self) -> None:
        cancelled = self._timers.cancel_all()
        for timer_id in cancelled:
            self._log_timer_cancel(timer_id)
        if cancelled:
            self._refresh_timers()

    def _tick_auto_enter(self) -> None:
        if self.hook is None or self.hook.paused:
            self._cancel_all_auto_enter()
            return
        updates = self._timers.tick(datetime.now(timezone.utc), time.monotonic())
        for update in updates:
            prefix = f"Таймер №{update.timer_id}"
            if update.kind == "started":
                self._append_log(f"{prefix}: начата серия Enter, {update.total} нажатий")
            elif update.kind == "completed":
                self._append_log(f"{prefix} завершён: {update.sent} нажатий")
            elif update.kind == "error":
                self._append_log(f"{prefix}: ошибка автоматического Enter: {update.detail}")
        if updates:
            self._refresh_timers()

    def _refresh_rules(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, rule in enumerate(self.cfg.rules):
            self.tree.insert("", "end", iid=str(index), values=(
                rule.key, rule.limit, rule.window_ms, rule.block_ms,
                "да" if rule.enabled else "нет",
            ))

    def _update_status(self) -> None:
        paused = self.hook.paused if self.hook else True
        self.status_label.config(text="Приостановлено" if paused else "Работает")
        self.toggle_btn.config(text="Старт" if paused else "Стоп")
        self._set_auto_controls()

    def _selected_index(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    # --- действия ---

    def _toggle(self) -> None:
        if self.hook:
            self.hook.set_paused(not self.hook.paused)
            if self.hook.paused:
                self._cancel_all_auto_enter()
        self._update_status()

    def _apply_rules(self) -> None:
        if self.hook:
            self.hook.set_rules(self.cfg.rules)

    def _add_rule(self) -> None:
        rule = self._ask_rule(None)
        if rule:
            self.cfg.rules.append(rule)
            self._after_change()

    def _edit_rule(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        rule = self._ask_rule(self.cfg.rules[index])
        if rule:
            self.cfg.rules[index] = rule
            self._after_change()

    def _delete_rule(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self.cfg.rules[index]
        self._after_change()

    def _after_change(self) -> None:
        self._refresh_rules()
        self._apply_rules()
        self._save(quiet=True)

    def _ask_rule(self, rule: Rule | None) -> Rule | None:
        dialog = RuleDialog(self.root, self.hook, rule)
        self._dialog = dialog
        self.root.wait_window(dialog)
        self._dialog = None
        return dialog.result

    def _save(self, quiet: bool = False) -> None:
        if not quiet:
            try:
                self.cfg.auto_enter = self._read_auto_settings()
            except ValueError as exc:
                messagebox.showerror("Ошибка", str(exc), parent=self.root)
                return
            self._show_auto_settings(self.cfg.auto_enter)
        config_module.save(self.cfg)
        if not quiet:
            self._append_log("Настройки сохранены в config.json")

    # --- события хука ---

    def _drain_events(self) -> None:
        if self.hook:
            while True:
                try:
                    event = self.hook.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(event)
        self._tick_auto_enter()
        self._update_status()
        self.root.after(50, self._drain_events)

    def _handle_event(self, event) -> None:
        if event.kind == "paused":
            self._cancel_all_auto_enter()
        if event.kind == "captured":
            if self._dialog is not None:
                self._dialog.on_captured(event.key)
            return
        messages = {
            "blocked": f"{event.key} — {event.detail}",
            "paused": "Пауза (Ctrl+Alt+P)",
            "resumed": "Работа возобновлена",
            "error": f"Ошибка: {event.detail}",
        }
        self._append_log(messages.get(event.kind, f"{event.kind}: {event.detail}"),
                         ts=event.ts or None)

    def _append_log(self, text: str, ts: float | None = None) -> None:
        # Время события из хука, а не момента опроса очереди (он отстаёт до 50 мс).
        stamp = format_log_time(time.time() if ts is None else ts)
        self.log.insert("end", f"{stamp}  {text}")
        if self.log.size() > LOG_LIMIT:
            self.log.delete(0, self.log.size() - LOG_LIMIT)
        self.log.see("end")

    # --- трей ---

    def hide_to_tray(self) -> None:
        if self._ensure_tray():
            self.root.withdraw()
        else:
            self.root.iconify()

    def show_window(self) -> None:
        self.root.after(0, self._show_window_impl)

    def _show_window_impl(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _ensure_tray(self) -> bool:
        if self._tray is not None:
            return True
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            return False

        image = Image.new("RGB", (64, 64), "#1f2933")
        draw = ImageDraw.Draw(image)
        draw.rectangle((14, 14, 50, 50), outline="#4fd1c5", width=5)
        draw.line((14, 50, 50, 14), fill="#f56565", width=5)

        menu = pystray.Menu(
            pystray.MenuItem("Открыть", lambda *_: self.show_window(), default=True),
            pystray.MenuItem("Пауза/Старт", lambda *_: self.root.after(0, self._toggle)),
            pystray.MenuItem("Выход", lambda *_: self.root.after(0, self.quit)),
        )
        self._tray = pystray.Icon("KeyLimiter", image, "KeyLimiter", menu)
        threading.Thread(target=self._tray.run, name="keylimiter-tray", daemon=True).start()
        return True

    def quit(self) -> None:
        self._cancel_all_auto_enter()
        self._save(quiet=True)
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        if self.hook:
            self.hook.stop()
        self.root.quit()
        self.root.destroy()

    def run(self, start_hidden: bool = False) -> None:
        if start_hidden:
            self.hide_to_tray()
        self.root.mainloop()
