"""Логика ограничения частоты нажатий. Без WinAPI — чистый Python, покрыта тестами."""

from collections import deque


class RateLimiter:
    """Скользящее окно + блокировка на фиксированное время.

    Не более ``limit`` нажатий за ``window_ms``. При превышении клавиша глушится
    на ``block_ms``; нажатия во время блокировки её НЕ продлевают.
    """

    def __init__(self, limit: int, window_ms: int, block_ms: int):
        if limit < 1:
            raise ValueError("limit должен быть >= 1")
        if window_ms <= 0:
            raise ValueError("window_ms должен быть > 0")
        if block_ms <= 0:
            raise ValueError("block_ms должен быть > 0")
        self.limit = limit
        self.window_ms = window_ms
        self.block_ms = block_ms
        self._hits: deque[float] = deque()
        self._blocked_until: float = 0.0

    def is_blocked(self, now_ms: float) -> bool:
        return now_ms < self._blocked_until

    def blocked_for(self, now_ms: float) -> float:
        """Сколько миллисекунд осталось до конца блокировки (0, если не заблокировано)."""
        return max(0.0, self._blocked_until - now_ms)

    def allow(self, now_ms: float) -> bool:
        """True — нажатие пропускается, False — глушится."""
        if now_ms < self._blocked_until:
            # Блокировка активна: глушим, но таймер не сдвигаем и нажатие не считаем.
            return False

        horizon = now_ms - self.window_ms
        while self._hits and self._hits[0] <= horizon:
            self._hits.popleft()

        if len(self._hits) >= self.limit:
            self._blocked_until = now_ms + self.block_ms
            self._hits.clear()
            return False

        self._hits.append(now_ms)
        return True

    def reset(self) -> None:
        self._hits.clear()
        self._blocked_until = 0.0
