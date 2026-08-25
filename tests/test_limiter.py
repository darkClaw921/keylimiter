"""Тесты логики лимитера. Не требуют Windows — гоняются и на macOS, и в CI."""

import pytest

from keylimiter.limiter import RateLimiter


def make(limit=6, window_ms=1000, block_ms=1000) -> RateLimiter:
    return RateLimiter(limit=limit, window_ms=window_ms, block_ms=block_ms)


def test_limit_passes_then_blocks():
    """6 нажатий за секунду проходят, 7-е глушится."""
    lim = make()
    for i in range(6):
        assert lim.allow(i * 10) is True, f"нажатие {i + 1} должно пройти"
    assert lim.allow(60) is False
    assert lim.is_blocked(60) is True


def test_block_is_not_extended_by_further_presses():
    """Ключевое требование: нажатия во время блокировки её не продлевают."""
    lim = make()
    for i in range(6):
        lim.allow(i * 10)
    assert lim.allow(60) is False          # сработал лимит, блок до 1060
    for t in range(100, 1060, 50):         # долбим всю секунду
        assert lim.allow(t) is False
    assert lim.blocked_for(1000) == pytest.approx(60)
    assert lim.allow(1060) is True         # ровно по истечении блокировки


def test_counter_is_clean_after_block():
    """После блокировки снова доступны полные 6 нажатий."""
    lim = make()
    for i in range(6):
        lim.allow(i * 10)
    lim.allow(60)
    for i in range(6):
        assert lim.allow(1060 + i * 10) is True
    assert lim.allow(1130) is False


def test_spread_out_presses_never_block():
    """По одному нажатию раз в 200 мс — блокировки не наступает никогда."""
    lim = make()
    for i in range(100):
        assert lim.allow(i * 200) is True


def test_sliding_window_drops_old_hits():
    """Старые нажатия выпадают из окна и не мешают новым."""
    lim = make(limit=3, window_ms=1000, block_ms=500)
    assert lim.allow(0) is True
    assert lim.allow(100) is True
    assert lim.allow(200) is True
    assert lim.allow(1150) is True   # 0 и 100 уже вне окна
    assert lim.allow(1160) is True   # 200 тоже вышло
    assert lim.allow(1170) is False  # 1150, 1160, 1170 -> лимит


def test_limit_one():
    lim = make(limit=1, window_ms=1000, block_ms=300)
    assert lim.allow(0) is True
    assert lim.allow(1) is False   # лимит сработал в t=1, блок до t=301
    assert lim.allow(300) is False
    assert lim.allow(301) is True


def test_tiny_window():
    """Крошечное окно: подряд нельзя, но с паузой в окно — можно."""
    lim = make(limit=2, window_ms=1, block_ms=100)
    assert lim.allow(0) is True
    assert lim.allow(0.5) is True
    assert lim.allow(0.9) is False
    assert lim.allow(101) is True
    assert lim.allow(300) is True


def test_reset_clears_state():
    lim = make(limit=2, window_ms=1000, block_ms=1000)
    lim.allow(0)
    lim.allow(1)
    assert lim.allow(2) is False
    lim.reset()
    assert lim.is_blocked(3) is False
    assert lim.allow(3) is True


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"window_ms": 0}, {"block_ms": 0}, {"window_ms": -5},
])
def test_invalid_parameters_rejected(kwargs):
    with pytest.raises(ValueError):
        make(**kwargs)
