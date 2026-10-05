"""Интеграция планировщика с GUI без создания окон и реального клавиатурного ввода."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("tkinter")

from keylimiter.config import Config  # noqa: E402
from keylimiter.gui import MSK_LABEL, App  # noqa: E402
from keylimiter.scheduler import AutoEnterSettings, EnterTimerGroup  # noqa: E402


@pytest.fixture
def app():
    app = App.__new__(App)
    app.cfg = Config(timers=[AutoEnterSettings(count=3, interval_ms=1)])
    app.hook = SimpleNamespace(paused=False)
    app._press_enter = Mock()
    app._timers = EnterTimerGroup(app._press_enter, app.cfg.timers)
    app._timers.arm(1, datetime.now(timezone.utc) - timedelta(days=2))
    app._set_auto_controls = Mock()
    app._refresh_timers = Mock()
    app._show_auto_settings = Mock()
    app._save = Mock()
    app.timer_tree = Mock()
    app.timer_tree.selection.return_value = ("1",)
    app.root = Mock()
    app._append_log = Mock()
    return app


def test_paused_hook_cancels_before_sending(app):
    app.hook.paused = True
    app._tick_auto_enter()
    assert not app._timers.timers[1].scheduler.active
    app._press_enter.assert_not_called()
    app._refresh_timers.assert_called_once()


def test_pause_event_cancels_waiting_job_even_if_hook_resumed(app):
    app._handle_event(SimpleNamespace(kind="paused", key="", detail="", ts=0))
    assert not app._timers.timers[1].scheduler.active
    app._press_enter.assert_not_called()


def test_completion_restores_controls_and_logs_total(app, monkeypatch):
    clock = iter([1, 2, 3])
    monkeypatch.setattr("keylimiter.gui.time.monotonic", lambda: next(clock))
    for _ in range(3):
        app._tick_auto_enter()
    assert app._press_enter.call_count == 3
    assert not app._timers.timers[1].scheduler.active
    assert app._timer_status(app._timers.timers[1]) == "Готово: 3 нажатий"
    app._append_log.assert_called_with("Таймер №1 завершён: 3 нажатий")


def test_send_error_stops_job_and_is_logged(app):
    app._press_enter.side_effect = OSError("SendInput failed")
    app._tick_auto_enter()
    app._tick_auto_enter()
    assert not app._timers.timers[1].scheduler.active
    assert app._press_enter.call_count == 1
    app._refresh_timers.assert_called_once()
    app._append_log.assert_called_with("Таймер №1: ошибка автоматического Enter: SendInput failed")


def test_cancel_stops_remaining_presses(app):
    app._tick_auto_enter()
    app._cancel_auto_enter()
    app._tick_auto_enter()
    assert app._press_enter.call_count == 1
    assert app._timer_status(app._timers.timers[1]) == "Отменён: отправлено 1"


def variable(value):
    return SimpleNamespace(get=lambda: value)


@pytest.mark.parametrize("zone, expected", [(MSK_LABEL, "Europe/Moscow"),
                                            ("Asia/Tokyo", "Asia/Tokyo")])
def test_read_time_to_seconds_and_timezone(app, zone, expected):
    app.auto_hour_var = variable("9")
    app.auto_minute_var = variable("5")
    app.auto_second_var = variable("37")
    app.auto_timezone_var = variable(zone)
    app.auto_count_var = variable("3")
    app.auto_interval_var = variable("100")
    settings = app._read_auto_settings()
    assert settings.time == "09:05:37"
    assert settings.timezone == expected


@pytest.mark.parametrize("second", ["60", "-1", "1.5", "", "abc", "001"])
def test_invalid_seconds_rejected(app, second):
    app.auto_hour_var = variable("12")
    app.auto_minute_var = variable("00")
    app.auto_second_var = variable(second)
    app.auto_timezone_var = variable(MSK_LABEL)
    app.auto_count_var = variable("3")
    app.auto_interval_var = variable("100")
    with pytest.raises(ValueError):
        app._read_auto_settings()


def test_add_timer_preserves_already_running_timer_and_saves_list(app):
    app._tick_auto_enter()
    settings = AutoEnterSettings(time="18:00:00", count=5, timezone="UTC")
    app._read_auto_settings = Mock(return_value=settings)
    app._add_auto_timer()
    assert list(app._timers.timers) == [1, 2]
    assert app._timers.timers[1].scheduler.sent == 1
    assert app._timers.timers[1].scheduler.active
    assert app.cfg.timers == [AutoEnterSettings(count=3, interval_ms=1), settings]
    app._save.assert_called_once_with(quiet=True)
    app.timer_tree.selection_set.assert_called_once_with("2")


def test_delete_selected_timer_leaves_other_timer_active(app):
    second = app._timers.add(AutoEnterSettings(count=5))
    app._timers.arm(second, datetime.now(timezone.utc) - timedelta(days=2))
    app._delete_auto_timer()
    assert list(app._timers.timers) == [2]
    assert app._timers.timers[2].scheduler.active
    assert app.cfg.timers == [AutoEnterSettings(count=5)]


def test_pause_event_cancels_all_timers(app):
    second = app._timers.add(AutoEnterSettings(time="23:59:59", count=5))
    app._timers.arm(second, datetime.now(timezone.utc))
    app._handle_event(SimpleNamespace(kind="paused", key="", detail="", ts=0))
    assert all(not timer.scheduler.active for timer in app._timers.timers.values())
    app._press_enter.assert_not_called()


def test_cancel_selected_timer_leaves_other_timer_active(app):
    second = app._timers.add(AutoEnterSettings(count=5))
    app._timers.arm(second, datetime.now(timezone.utc))
    app._cancel_auto_enter()
    assert not app._timers.timers[1].scheduler.active
    assert app._timers.timers[2].scheduler.active


def test_plan_all_only_arms_inactive_timers(app):
    original_target = app._timers.timers[1].scheduler.target
    second = app._timers.add(AutoEnterSettings(time="23:59:59", count=5))
    app._arm_all_auto_enter()
    assert app._timers.timers[1].scheduler.target == original_target
    assert app._timers.timers[2].scheduler.active
    assert app._timers.timers[2].phase == "waiting"


def test_edit_cancelled_timer_updates_saved_settings(app):
    app._cancel_auto_enter()
    settings = AutoEnterSettings(time="18:00:00", count=5, timezone="UTC")
    app._read_auto_settings = Mock(return_value=settings)
    app._edit_auto_timer()
    assert app.cfg.timers == [settings]
    assert app._timers.timers[1].phase == "idle"
    app._save.assert_called_once_with(quiet=True)
