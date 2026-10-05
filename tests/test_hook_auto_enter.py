"""На Windows проверяем взаимодействие автоматического ввода с реальным хуком."""

import ctypes
import sys
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows hook")


def test_own_enter_bypasses_limiter_and_preserves_physical_key_state():
    from keylimiter.config import Rule
    from keylimiter.hook import (
        KBDLLHOOKSTRUCT, LLKHF_INJECTED, WM_KEYDOWN, WM_KEYUP, KeyboardHook,
    )
    from keylimiter.wininput import AUTO_ENTER_MARKER, VK_RETURN

    hook = KeyboardHook([Rule(key="ENTER", limit=1)])
    user32 = Mock()
    data = KBDLLHOOKSTRUCT(vkCode=VK_RETURN, flags=LLKHF_INJECTED,
                          dwExtraInfo=AUTO_ENTER_MARKER)
    hook._down.add(VK_RETURN)
    hook._suppressed.add(VK_RETURN)
    hook.capture_next_key()
    for _ in range(10):
        for message in (WM_KEYDOWN, WM_KEYUP):
            assert hook._handle_event(user32, message, ctypes.addressof(data)) is False
    assert hook._down == {VK_RETURN}
    assert hook._suppressed == {VK_RETURN}
    assert hook._capture_once
    assert hook.events.empty()
    assert hook._limiters[VK_RETURN][1].allow(0)


def test_other_injected_enter_still_obeys_limiter():
    from keylimiter.config import Rule
    from keylimiter.hook import (
        KBDLLHOOKSTRUCT, LLKHF_INJECTED, WM_KEYDOWN, WM_KEYUP, KeyboardHook,
    )
    from keylimiter.wininput import VK_RETURN

    hook = KeyboardHook([Rule(key="ENTER", limit=1)])
    user32 = Mock()
    data = KBDLLHOOKSTRUCT(vkCode=VK_RETURN, flags=LLKHF_INJECTED, dwExtraInfo=123)
    assert hook._handle_event(user32, WM_KEYDOWN, ctypes.addressof(data)) is False
    assert hook._handle_event(user32, WM_KEYUP, ctypes.addressof(data)) is False
    assert hook._handle_event(user32, WM_KEYDOWN, ctypes.addressof(data)) is True
    assert hook._handle_event(user32, WM_KEYUP, ctypes.addressof(data)) is True
