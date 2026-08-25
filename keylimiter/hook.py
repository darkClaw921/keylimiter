"""Низкоуровневый клавиатурный хук Windows (WH_KEYBOARD_LL) на голом ctypes.

Только низкоуровневый хук позволяет ПОДАВИТЬ нажатие: возвращаем 1 вместо
вызова CallNextHookEx. Хук обязан жить в потоке с циклом сообщений, поэтому
работает в отдельном threading.Thread; GUI общается с ним через set_rules()
и очередь событий.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import queue
import threading
import time
from dataclasses import dataclass

from .config import Rule
from .keys import name_from_vk
from .limiter import RateLimiter

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
HC_ACTION = 0
LLKHF_INJECTED = 0x00000010

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10

_DOWN_MESSAGES = (WM_KEYDOWN, WM_SYSKEYDOWN)
_UP_MESSAGES = (WM_KEYUP, WM_SYSKEYUP)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


@dataclass
class HookEvent:
    """Событие для GUI-лога."""

    kind: str  # "blocked" | "captured" | "paused" | "resumed" | "error"
    key: str = ""
    detail: str = ""
    ts: float = 0.0


def _load_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    user32.SetWindowsHookExW.restype = wt.HHOOK
    user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
    user32.UnhookWindowsHookEx.restype = wt.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = wt.BOOL
    user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostThreadMessageW.restype = wt.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return user32


def now_ms() -> float:
    """Монотонное время в миллисекундах."""
    return time.perf_counter() * 1000.0


class KeyboardHook:
    """Перехватывает клавиши и глушит те, что превысили лимит."""

    def __init__(self, rules: list[Rule], count_injected: bool = True,
                 pause_hotkey_vk: int | None = ord("P")) -> None:
        self.events: queue.Queue[HookEvent] = queue.Queue(maxsize=500)
        self._lock = threading.Lock()
        self._limiters: dict[int, tuple[Rule, RateLimiter]] = {}
        self._count_injected = count_injected
        self._pause_hotkey_vk = pause_hotkey_vk
        self._paused = False

        self._down: set[int] = set()       # клавиши, чей KEYDOWN мы пропустили
        self._suppressed: set[int] = set()  # клавиши, чей KEYDOWN мы подавили

        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hook_handle = None
        self._proc = None  # ссылку на трамплин держим сами, иначе GC его снесёт
        self._ready = threading.Event()
        self._capture_once: bool = False

        self.set_rules(rules)

    # --- публичный API (вызывается из GUI-потока) ---

    def set_rules(self, rules: list[Rule]) -> None:
        with self._lock:
            limiters: dict[int, tuple[Rule, RateLimiter]] = {}
            for rule in rules:
                vk = rule.vk
                if vk is None or not rule.enabled:
                    continue
                limiters[vk] = (rule, RateLimiter(rule.limit, rule.window_ms, rule.block_ms))
            self._limiters = limiters

    def set_count_injected(self, value: bool) -> None:
        with self._lock:
            self._count_injected = value

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def set_paused(self, value: bool) -> None:
        with self._lock:
            self._paused = value
            for _rule, limiter in self._limiters.values():
                limiter.reset()
        self._emit(HookEvent(kind="paused" if value else "resumed", ts=time.time()))

    def capture_next_key(self) -> None:
        """Следующее нажатие уйдёт в очередь как событие 'captured' (для диалога записи клавиши)."""
        with self._lock:
            self._capture_once = True

    def cancel_capture(self) -> None:
        with self._lock:
            self._capture_once = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="keylimiter-hook", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        thread, thread_id = self._thread, self._thread_id
        if thread and thread_id:
            user32 = _load_user32()
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            thread.join(timeout=3)
        self._thread = None
        self._thread_id = None

    # --- внутреннее ---

    def _emit(self, event: HookEvent) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            pass

    def _run(self) -> None:
        try:
            user32 = _load_user32()
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._thread_id = kernel32.GetCurrentThreadId()

            self._proc = HOOKPROC(self._make_callback(user32))
            self._hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
            if not self._hook_handle:
                err = ctypes.get_last_error()
                self._emit(HookEvent(kind="error", detail=f"SetWindowsHookExW failed: {err}",
                                     ts=time.time()))
                return
        except Exception as exc:  # noqa: BLE001 — поток не должен падать молча
            self._emit(HookEvent(kind="error", detail=str(exc), ts=time.time()))
            return
        finally:
            self._ready.set()

        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None

    def _make_callback(self, user32):
        def callback(n_code, w_param, l_param):
            if n_code != HC_ACTION:
                return user32.CallNextHookEx(None, n_code, w_param, l_param)
            try:
                if self._handle_event(user32, w_param, l_param):
                    return 1  # подавляем событие
            except Exception as exc:  # noqa: BLE001 — сбой хука не должен ломать ввод
                log.exception("Ошибка в обработчике хука")
                self._emit(HookEvent(kind="error", detail=str(exc), ts=time.time()))
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        return callback

    def _handle_event(self, user32, w_param, l_param) -> bool:
        """True — событие нужно подавить."""
        data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = int(data.vkCode)
        is_down = w_param in _DOWN_MESSAGES
        is_up = w_param in _UP_MESSAGES
        if not (is_down or is_up):
            return False

        # Режим записи клавиши: отдаём код в GUI и пропускаем событие дальше.
        with self._lock:
            capturing = self._capture_once
        if capturing and is_down:
            with self._lock:
                self._capture_once = False
            self._emit(HookEvent(kind="captured", key=name_from_vk(vk), ts=time.time()))
            return False

        if is_down and self._check_pause_hotkey(user32, vk):
            self.set_paused(not self.paused)
            self._suppressed.add(vk)  # чтобы парный KEYUP тоже не ушёл в приложение
            return True

        if is_up:
            self._down.discard(vk)
            # KEYUP от подавленного KEYDOWN тоже глушим, иначе окно получит
            # «отпускание без нажатия».
            if vk in self._suppressed:
                self._suppressed.discard(vk)
                return True
            return False

        # --- дальше только KEYDOWN ---
        with self._lock:
            if self._paused:
                return False
            entry = self._limiters.get(vk)
            count_injected = self._count_injected

        if entry is None:
            return False

        if not count_injected and (data.flags & LLKHF_INJECTED):
            return False

        # Авто-повтор при удержании: клавиша уже нажата — пропускаем, не считая.
        if vk in self._down:
            return False

        rule, limiter = entry
        if limiter.allow(now_ms()):
            self._down.add(vk)
            return False

        self._suppressed.add(vk)
        self._emit(HookEvent(
            kind="blocked",
            key=rule.key,
            detail=f"заблокировано на {int(limiter.blocked_for(now_ms()))} мс",
            ts=time.time(),
        ))
        return True

    def _check_pause_hotkey(self, user32, vk: int) -> bool:
        if self._pause_hotkey_vk is None or vk != self._pause_hotkey_vk:
            return False
        ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
        alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000
        return bool(ctrl and alt)
