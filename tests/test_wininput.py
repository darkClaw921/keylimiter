"""Проверка WinAPI-структур и пар нажатие/отпускание с подменой SendInput."""

import ctypes

import pytest

from keylimiter.wininput import (
    AUTO_ENTER_MARKER, INPUT, INPUT_KEYBOARD, KEYEVENTF_KEYUP, VK_RETURN, EnterSender,
)


def test_input_has_native_windows_size():
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_press_sends_tagged_down_and_up():
    calls = []

    def send(count, inputs, size):
        calls.append((count, size))
        assert count == 2
        for event in inputs:
            assert event.type == INPUT_KEYBOARD
            assert event.ki.wVk == VK_RETURN
            assert event.ki.dwExtraInfo == AUTO_ENTER_MARKER
        assert inputs[0].ki.dwFlags == 0
        assert inputs[1].ki.dwFlags == KEYEVENTF_KEYUP
        return count

    sender = EnterSender.__new__(EnterSender)
    sender._send_input = send
    sender.press_enter()
    assert calls == [(2, ctypes.sizeof(INPUT))]


@pytest.mark.parametrize("sent", [0, 1])
def test_send_failure_reported_and_partial_keydown_released(monkeypatch, sent):
    calls = []
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)

    def send(count, inputs, size):
        calls.append(count)
        if count == 1:
            assert inputs.contents.ki.dwFlags == KEYEVENTF_KEYUP
            assert inputs.contents.ki.dwExtraInfo == AUTO_ENTER_MARKER
            return 1
        return sent

    sender = EnterSender.__new__(EnterSender)
    sender._send_input = send
    with pytest.raises(OSError, match=f"{sent}/2, код ошибки 5"):
        sender.press_enter()
    assert calls == ([2, 1] if sent == 1 else [2])
