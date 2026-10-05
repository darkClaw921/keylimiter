"""Независимое выполнение, отмена и правка нескольких таймеров."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from keylimiter.scheduler import AutoEnterSettings, EnterTimerGroup

NOW = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)


def test_simultaneous_timers_send_sum_of_counts_once():
    sender = Mock()
    group = EnterTimerGroup(sender, [
        AutoEnterSettings(time="12:00:00", count=3, interval_ms=200),
        AutoEnterSettings(time="09:00:00", timezone="UTC", count=2, interval_ms=400),
    ])
    for timer_id in group.timers:
        group.arm(timer_id, NOW)
    updates = group.tick(NOW, 0)
    assert [(item.timer_id, item.kind) for item in updates] == [(1, "started"), (2, "started")]
    assert sender.call_count == 2
    group.tick(NOW, 0.2)
    assert sender.call_count == 3
    group.tick(NOW, 0.4)
    assert sender.call_count == 5
    assert [timer.scheduler.sent for timer in group.timers.values()] == [3, 2]
    assert all(timer.phase == "completed" for timer in group.timers.values())
    assert group.tick(NOW + timedelta(days=1), 100000) == []
    assert sender.call_count == 5


def test_different_start_times_do_not_replace_each_other():
    sender = Mock()
    group = EnterTimerGroup(sender, [
        AutoEnterSettings(time="12:00:00"), AutoEnterSettings(time="12:00:01"),
    ])
    group.arm(1, NOW)
    group.arm(2, NOW)
    assert [item.timer_id for item in group.tick(NOW, 0)] == [1]
    assert group.timers[2].phase == "waiting"
    assert [item.timer_id for item in group.tick(NOW + timedelta(seconds=1), 1)] == [2]
    assert sender.call_count == 2


def test_cancel_one_running_timer_leaves_other_running():
    sender = Mock()
    group = EnterTimerGroup(sender, [AutoEnterSettings(count=3), AutoEnterSettings(count=3)])
    group.arm(1, NOW)
    group.arm(2, NOW)
    group.tick(NOW, 0)
    assert group.cancel(1)
    assert not group.cancel(1)
    group.tick(NOW, 1)
    group.tick(NOW, 2)
    assert group.timers[1].scheduler.sent == 1
    assert group.timers[1].phase == "cancelled"
    assert group.timers[2].scheduler.sent == 3
    assert group.timers[2].phase == "completed"
    assert sender.call_count == 4


def test_cancel_all_stops_waiting_and_running_timers():
    sender = Mock()
    group = EnterTimerGroup(sender, [AutoEnterSettings(count=3),
                                     AutoEnterSettings(time="13:00:00")])
    group.arm(1, NOW)
    group.arm(2, NOW)
    group.tick(NOW, 0)
    assert group.cancel_all() == [1, 2]
    assert group.cancel_all() == []
    assert group.tick(NOW + timedelta(hours=2), 7200) == []
    assert sender.call_count == 1


def test_deleting_active_timer_does_not_change_other_ids_or_progress():
    sender = Mock()
    group = EnterTimerGroup(sender, [AutoEnterSettings(count=3), AutoEnterSettings(count=2)])
    group.arm(1, NOW)
    group.arm(2, NOW)
    group.tick(NOW, 0)
    group.remove(1)
    new_id = group.add(AutoEnterSettings(time="13:00:00"))
    assert new_id == 3
    assert list(group.timers) == [2, 3]
    group.tick(NOW, 1)
    assert group.timers[2].scheduler.sent == 2
    assert group.timers[3].phase == "idle"
    assert sender.call_count == 3


def test_active_edit_and_rearm_rejected_without_changing_schedule():
    group = EnterTimerGroup(Mock(), [AutoEnterSettings(count=3)])
    target = group.arm(1, NOW)
    with pytest.raises(ValueError, match="отмените"):
        group.edit(1, AutoEnterSettings(time="13:00:00"))
    with pytest.raises(ValueError, match="уже запланирован"):
        group.arm(1, NOW)
    assert group.timers[1].scheduler.target == target
    assert group.timers[1].settings.count == 3


def test_edit_cancelled_timer_and_rearm_resets_progress():
    sender = Mock()
    group = EnterTimerGroup(sender, [AutoEnterSettings(count=3)])
    group.arm(1, NOW)
    group.tick(NOW, 0)
    group.cancel(1)
    group.edit(1, AutoEnterSettings(count=1))
    assert group.timers[1].scheduler.sent == 0
    group.arm(1, NOW)
    group.tick(NOW, 1)
    assert group.timers[1].phase == "completed"
    assert sender.call_count == 2


def test_error_in_one_timer_does_not_stop_other_timers():
    sender = Mock(side_effect=[OSError("failed"), None])
    group = EnterTimerGroup(sender, [AutoEnterSettings(), AutoEnterSettings()])
    group.arm(1, NOW)
    group.arm(2, NOW)
    updates = group.tick(NOW, 0)
    assert [(item.timer_id, item.kind) for item in updates] == [(1, "error"), (2, "completed")]
    assert updates[0].detail == "failed"
    assert group.timers[1].scheduler.sent == 0
    assert group.timers[2].scheduler.sent == 1
    assert group.tick(NOW, 1) == []
    assert sender.call_count == 2


def test_settings_are_independent_snapshots():
    original = AutoEnterSettings(count=3)
    group = EnterTimerGroup(Mock(), [original, original])
    original.count = 10
    group.edit(1, AutoEnterSettings(count=5))
    assert group.timers[1].settings.count == 5
    assert group.timers[2].settings.count == 3


def test_sender_required_for_arming():
    group = EnterTimerGroup(None, [AutoEnterSettings()])
    with pytest.raises(ValueError, match="недоступна"):
        group.arm(1, NOW)
    assert group.timers[1].phase == "idle"
