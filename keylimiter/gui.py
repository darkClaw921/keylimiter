"""Окно настройки правил на tkinter + сворачивание в трей."""

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import config as config_module
from .config import Config, Rule
from .keys import ALL_KEY_NAMES, vk_from_name

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
    def __init__(self, cfg: Config, hook) -> None:
        self.cfg = cfg
        self.hook = hook
        self._tray = None
        self._dialog: RuleDialog | None = None

        self.root = tk.Tk()
        self.root.title("KeyLimiter — ограничитель частоты нажатий")
        self.root.minsize(640, 460)

        self._build_ui()
        self._refresh_rules()
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

        self.tree = ttk.Treeview(main, columns=[c[0] for c in COLUMNS], show="headings", height=8)
        for name, title, width in COLUMNS:
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=(10, 6))
        self.tree.bind("<Double-1>", lambda _e: self._edit_rule())

        buttons = ttk.Frame(main)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Добавить", command=self._add_rule).pack(side="left")
        ttk.Button(buttons, text="Изменить", command=self._edit_rule).pack(side="left", padx=6)
        ttk.Button(buttons, text="Удалить", command=self._delete_rule).pack(side="left")
        ttk.Button(buttons, text="Свернуть в трей", command=self.hide_to_tray).pack(side="right")
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right", padx=6)

        ttk.Label(main, text="Журнал:").pack(anchor="w", pady=(10, 2))
        self.log = tk.Listbox(main, height=8)
        self.log.pack(fill="both", expand=True)

        hint = ("Пауза/возобновление — Ctrl+Alt+P. Закрытие окна сворачивает в трей; "
                "выход — из меню трея.")
        ttk.Label(main, text=hint, foreground="#666").pack(anchor="w", pady=(6, 0))

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

    def _selected_index(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    # --- действия ---

    def _toggle(self) -> None:
        if self.hook:
            self.hook.set_paused(not self.hook.paused)
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
        self._update_status()
        self.root.after(50, self._drain_events)

    def _handle_event(self, event) -> None:
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
