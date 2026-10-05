"""Проверки времени запуска, числа Enter, интервалов и отмены без реального ввода."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from keylimiter.scheduler import (
    AutoEnterSettings, EnterScheduler, normalize_time, normalize_timezone,
)


def msk(*args):
    return datetime(*args, tzinfo=ZoneInfo("Europe/Moscow"))


@pytest.mark.parametrize("value, expected", [
    ("09:05", "09:05:00"), (" 23:59:59 ", "23:59:59"), ("00:00:00", "00:00:00"),
])
def test_normalize_time(value, expected):
    assert normalize_time(value) == expected


@pytest.mark.parametrize("value", [
    "24:00", "12:60", "12:00:60", "9:05", "12", "12:01:02:03", "", None, 1200,
])
def test_invalid_time(value):
    with pytest.raises(ValueError):
        normalize_time(value)


@pytest.mark.parametrize("kwargs", [
    {"count": 0}, {"count": -1}, {"count": True}, {"count": "3"},
    {"interval_ms": 0}, {"interval_ms": False}, {"interval_ms": 1.5},
])
def test_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        AutoEnterSettings(**kwargs)


def test_waits_until_target_and_sends_exact_count():
    presses = []
    scheduler = EnterScheduler(lambda: presses.append("ENTER"))
    now = msk(2026, 10, 5, 12, 0, 0)
    settings = AutoEnterSettings(time="12:00:01", count=3, interval_ms=200)
    assert scheduler.arm(settings, now) == msk(2026, 10, 5, 12, 0, 1)
    assert not scheduler.tick(now, 10)
    assert presses == []
    due = msk(2026, 10, 5, 12, 0, 1)
    assert scheduler.tick(due, 11)
    assert scheduler.running
    assert not scheduler.tick(due, 11.1)
    assert scheduler.tick(due, 11.2)
    assert not scheduler.tick(due, 11.3)
    assert scheduler.tick(due, 11.5)
    assert not scheduler.active
    assert scheduler.sent == 3
    assert not scheduler.tick(msk(2026, 10, 6, 12, 0, 1), 90000)
    assert presses == ["ENTER"] * 3


def test_past_time_schedules_tomorrow_across_year_boundary():
    scheduler = EnterScheduler(lambda: None)
    target = scheduler.arm(AutoEnterSettings(time="10:30"), msk(2026, 12, 31, 23, 59))
    assert target == msk(2027, 1, 1, 10, 30)


def test_exact_target_time_can_start_immediately():
    presses = []
    scheduler = EnterScheduler(lambda: presses.append(1))
    now = msk(2026, 10, 5, 12)
    scheduler.arm(AutoEnterSettings(count=1), now)
    assert scheduler.tick(now, 0)
    assert not scheduler.active
    assert presses == [1]


@pytest.mark.parametrize("start_series", [False, True])
def test_cancel_waiting_or_running_series(start_series):
    presses = []
    scheduler = EnterScheduler(lambda: presses.append(1))
    now = msk(2026, 10, 5, 12)
    scheduler.arm(AutoEnterSettings(count=10), now)
    if start_series:
        scheduler.tick(now, 1)
    scheduler.cancel()
    assert not scheduler.tick(msk(2026, 10, 6, 12), 90000)
    assert len(presses) == int(start_series)


def test_delayed_poll_sends_one_press_instead_of_catch_up_burst():
    presses = []
    scheduler = EnterScheduler(lambda: presses.append(1))
    now = msk(2026, 10, 5, 12)
    scheduler.arm(AutoEnterSettings(count=5, interval_ms=100), now)
    scheduler.tick(now, 1)
    scheduler.tick(now, 60)
    assert len(presses) == 2
    assert not scheduler.tick(now, 60.01)


def test_running_intervals_ignore_wall_clock_changes():
    presses = []
    scheduler = EnterScheduler(lambda: presses.append(1))
    now = msk(2026, 10, 5, 12)
    scheduler.arm(AutoEnterSettings(count=3, interval_ms=500), now)
    scheduler.tick(now, 1)
    assert not scheduler.tick(msk(2026, 10, 6, 12), 1.1)
    assert scheduler.tick(msk(2026, 10, 4, 12), 1.5)
    assert len(presses) == 2


def test_settings_are_snapshotted_on_arm():
    scheduler = EnterScheduler(lambda: None)
    now = msk(2026, 10, 5, 12)
    settings = AutoEnterSettings(count=2)
    scheduler.arm(settings, now)
    settings.count = 10
    scheduler.tick(now, 1)
    scheduler.tick(now, 2)
    assert scheduler.sent == 2
    assert not scheduler.active


def test_rearming_replaces_previous_series():
    scheduler = EnterScheduler(lambda: None)
    now = msk(2026, 10, 5, 12)
    scheduler.arm(AutoEnterSettings(count=10), now)
    scheduler.tick(now, 1)
    scheduler.arm(AutoEnterSettings(time="13:00", count=1), now)
    assert scheduler.sent == 0
    assert not scheduler.running
    assert not scheduler.tick(now, 2)
    assert scheduler.tick(msk(2026, 10, 5, 13), 3601)
    assert not scheduler.active


def test_send_error_cancels_series_without_retrying():
    calls = []

    def press():
        calls.append(1)
        if len(calls) == 2:
            raise OSError("input failed")

    scheduler = EnterScheduler(press)
    now = msk(2026, 10, 5, 12)
    scheduler.arm(AutoEnterSettings(count=3), now)
    scheduler.tick(now, 1)
    with pytest.raises(OSError, match="input failed"):
        scheduler.tick(now, 2)
    assert scheduler.sent == 1
    assert not scheduler.active
    assert not scheduler.tick(now, 3)
    assert len(calls) == 2


@pytest.mark.parametrize("value, expected", [
    ("MSK", "Europe/Moscow"), (" msk ", "Europe/Moscow"),
    ("utc", "UTC"), ("Asia/Tokyo", "Asia/Tokyo"),
])
def test_timezone_names(value, expected):
    assert normalize_timezone(value) == expected


@pytest.mark.parametrize("value", [None, 3, "", "Unknown/Zone", "../Europe/Moscow"])
def test_invalid_timezone(value):
    with pytest.raises(ValueError):
        AutoEnterSettings(timezone=value)


def test_default_is_moscow_independently_of_computer_timezone():
    scheduler = EnterScheduler(lambda: None)
    now = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)
    target = scheduler.arm(AutoEnterSettings(time="12:15:37"), now)
    assert target == msk(2026, 10, 5, 12, 15, 37)
    assert target.astimezone(timezone.utc) == datetime(2026, 10, 5, 9, 15, 37, tzinfo=timezone.utc)
    assert not scheduler.tick(datetime(2026, 10, 5, 9, 15, 36, tzinfo=timezone.utc), 1)
    assert scheduler.tick(datetime(2026, 10, 5, 9, 15, 37, tzinfo=timezone.utc), 2)


def test_target_day_is_taken_from_selected_zone():
    scheduler = EnterScheduler(lambda: None)
    now = datetime(2026, 10, 5, 23, tzinfo=timezone.utc)
    target = scheduler.arm(AutoEnterSettings(time="09:10:11", timezone="Asia/Tokyo"), now)
    assert target == datetime(2026, 10, 6, 9, 10, 11, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert target.astimezone(timezone.utc) == datetime(2026, 10, 6, 0, 10, 11, tzinfo=timezone.utc)


def test_tick_accepts_other_timezones_for_same_instant():
    scheduler = EnterScheduler(lambda: None)
    now = datetime(2026, 10, 5, 11, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    scheduler.arm(AutoEnterSettings(time="06:00:05"), now)
    assert not scheduler.tick(datetime(2026, 10, 5, 3, 0, 4, tzinfo=timezone.utc), 1)
    assert scheduler.tick(datetime(2026, 10, 5, 12, 0, 5, tzinfo=ZoneInfo("Asia/Tokyo")), 2)


def test_naive_datetime_rejected_instead_of_using_computer_timezone():
    scheduler = EnterScheduler(lambda: None)
    with pytest.raises(ValueError, match="часовой пояс"):
        scheduler.arm(AutoEnterSettings(), datetime(2026, 10, 5, 12))
    scheduler.arm(AutoEnterSettings(), msk(2026, 10, 5, 11))
    with pytest.raises(ValueError, match="часовой пояс"):
        scheduler.tick(datetime(2026, 10, 5, 12), 1)


def test_missing_spring_clock_time_skips_to_next_day():
    scheduler = EnterScheduler(lambda: None)
    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 3, 8, 0, tzinfo=zone)
    target = scheduler.arm(AutoEnterSettings(time="02:30:00", timezone=zone.key), now)
    assert target == datetime(2026, 3, 9, 2, 30, tzinfo=zone)


def test_repeated_autumn_time_uses_next_occurrence():
    scheduler = EnterScheduler(lambda: None)
    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
    target = scheduler.arm(AutoEnterSettings(time="01:30:00", timezone=zone.key), now)
    assert target.fold == 1
    assert target.astimezone(timezone.utc) == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    assert not scheduler.tick(datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc), 1)
    assert scheduler.tick(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), 2)
    assert not scheduler.tick(datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc), 3)
