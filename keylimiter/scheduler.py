"""Однократный запуск серии Enter в выбранном часовом поясе. Без WinAPI и ожиданий."""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Moscow"


def normalize_timezone(value: str) -> str:
    """Проверяет IANA-пояс; короткое имя MSK означает Europe/Moscow."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Выберите часовой пояс.")
    key = value.strip()
    key = {"MSK": DEFAULT_TIMEZONE, "UTC": "UTC"}.get(key.upper(), key)
    try:
        ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Неизвестный часовой пояс: {value}") from None
    return key


def _as_utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Текущее время должно содержать часовой пояс.")
    return now.astimezone(timezone.utc)


def _next_target(settings: "AutoEnterSettings", now: datetime) -> datetime:
    """Ближайший существующий момент ЧЧ:ММ:СС, включая переходы летнего времени."""
    now_utc = _as_utc(now)
    zone = ZoneInfo(settings.timezone)
    hour, minute, second = map(int, settings.time.split(":"))
    wall = now_utc.astimezone(zone).replace(
        hour=hour, minute=minute, second=second, microsecond=0, tzinfo=None, fold=0,
    )
    while True:
        candidates = []
        for fold in (0, 1):
            candidate = wall.replace(tzinfo=zone, fold=fold)
            utc = candidate.astimezone(timezone.utc)
            # При переводе часов вперёд некоторые местные времена не существуют.
            if utc.astimezone(zone).replace(tzinfo=None) == wall and utc >= now_utc:
                candidates.append(candidate)
        if candidates:
            # При переводе назад выбираем ближайшее из двух совпадающих времён.
            return min(candidates, key=lambda value: value.astimezone(timezone.utc))
        wall += timedelta(days=1)


def normalize_time(value: str) -> str:
    """Принимает ЧЧ:ММ или ЧЧ:ММ:СС и возвращает ЧЧ:ММ:СС."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", value.strip()
    ):
        raise ValueError("Время должно быть в формате ЧЧ:ММ или ЧЧ:ММ:СС.")
    parts = [int(part) for part in value.strip().split(":")]
    hour, minute = parts[:2]
    second = parts[2] if len(parts) == 3 else 0
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError("Часы: 00–23; минуты и секунды: 00–59.")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


@dataclass
class AutoEnterSettings:
    time: str = "12:00:00"
    count: int = 1
    interval_ms: int = 200
    timezone: str = DEFAULT_TIMEZONE

    def __post_init__(self) -> None:
        self.time = normalize_time(self.time)
        self.timezone = normalize_timezone(self.timezone)
        for field, label in (("count", "Количество нажатий"),
                             ("interval_ms", "Интервал, мс")):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"«{label}» должно быть целым числом больше нуля.")


class EnterScheduler:
    """tick() отправляет максимум одно нажатие, не блокируя интерфейс.

    До старта сравниваются моменты UTC, интервалы серии отсчитываются
    монотонными часами. Пропущенные интервалы не догоняются пачкой.
    """

    def __init__(self, press_enter: Callable[[], None]) -> None:
        self._press_enter = press_enter
        self._settings: AutoEnterSettings | None = None
        self.target: datetime | None = None
        self.sent = 0
        self._next_press: float | None = None

    @property
    def active(self) -> bool:
        return self._settings is not None

    @property
    def running(self) -> bool:
        return self.active and self._next_press is not None

    def arm(self, settings: AutoEnterSettings, now: datetime) -> datetime:
        snapshot = replace(settings)
        target = _next_target(snapshot, now)
        self._settings = snapshot
        self.target = target
        self.sent = 0
        self._next_press = None
        return target

    def cancel(self) -> None:
        self._settings = None
        self.target = None
        self._next_press = None

    def tick(self, now: datetime, monotonic: float) -> bool:
        """True, если отправлена пара нажатие/отпускание. Ошибка отменяет серию."""
        settings = self._settings
        if settings is None:
            return False
        if self._next_press is None:
            if _as_utc(now) < self.target.astimezone(timezone.utc):
                return False
        elif monotonic < self._next_press:
            return False

        try:
            self._press_enter()
        except Exception:
            self.cancel()
            raise
        self.sent += 1
        if self.sent == settings.count:
            self.cancel()
        else:
            self._next_press = monotonic + settings.interval_ms / 1000
        return True


@dataclass
class EnterTimer:
    id: int
    settings: AutoEnterSettings
    scheduler: EnterScheduler
    phase: str = "idle"
    error: str = ""


@dataclass
class TimerUpdate:
    timer_id: int
    kind: str
    sent: int
    total: int
    detail: str = ""


class EnterTimerGroup:
    """Независимые однократные таймеры со стабильными идентификаторами."""

    def __init__(self, press_enter: Callable[[], None] | None,
                 settings: list[AutoEnterSettings] | None = None) -> None:
        self._press_enter = press_enter
        self.timers: dict[int, EnterTimer] = {}
        self._next_id = 1
        for item in settings or []:
            self.add(item)

    def _send(self) -> None:
        if self._press_enter is None:
            raise OSError("Отправка Enter недоступна.")
        self._press_enter()

    def add(self, settings: AutoEnterSettings) -> int:
        timer_id = self._next_id
        timer = EnterTimer(timer_id, replace(settings), EnterScheduler(self._send))
        self.timers[timer_id] = timer
        self._next_id += 1
        return timer_id

    def edit(self, timer_id: int, settings: AutoEnterSettings) -> None:
        timer = self.timers[timer_id]
        if timer.scheduler.active:
            raise ValueError("Сначала отмените выбранный таймер.")
        timer.settings = replace(settings)
        timer.scheduler = EnterScheduler(self._send)
        timer.phase = "idle"
        timer.error = ""

    def remove(self, timer_id: int) -> None:
        self.timers[timer_id].scheduler.cancel()
        del self.timers[timer_id]

    def arm(self, timer_id: int, now: datetime) -> datetime:
        timer = self.timers[timer_id]
        if self._press_enter is None:
            raise ValueError("Отправка Enter недоступна.")
        if timer.scheduler.active:
            raise ValueError("Таймер уже запланирован.")
        target = timer.scheduler.arm(timer.settings, now)
        timer.phase = "waiting"
        timer.error = ""
        return target

    def cancel(self, timer_id: int) -> bool:
        timer = self.timers[timer_id]
        if not timer.scheduler.active:
            return False
        timer.scheduler.cancel()
        timer.phase = "cancelled"
        return True

    def cancel_all(self) -> list[int]:
        return [timer_id for timer_id in self.timers if self.cancel(timer_id)]

    def tick(self, now: datetime, monotonic: float) -> list[TimerUpdate]:
        updates = []
        for timer in self.timers.values():
            if not timer.scheduler.active:
                continue
            try:
                pressed = timer.scheduler.tick(now, monotonic)
            except Exception as exc:
                timer.scheduler.cancel()
                timer.phase = "error"
                timer.error = str(exc)
                updates.append(TimerUpdate(timer.id, "error", timer.scheduler.sent,
                                           timer.settings.count, timer.error))
                continue
            if pressed:
                timer.phase = "running" if timer.scheduler.active else "completed"
                kind = "completed" if timer.phase == "completed" else (
                    "started" if timer.scheduler.sent == 1 else "progress"
                )
                updates.append(TimerUpdate(timer.id, kind, timer.scheduler.sent,
                                           timer.settings.count))
        return updates
