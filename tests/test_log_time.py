"""Формат времени в журнале: часы, минуты, секунды и миллисекунды."""

import re
import time

import pytest

pytest.importorskip("tkinter")

from keylimiter.gui import format_log_time  # noqa: E402


def test_milliseconds_are_shown():
    ts = time.mktime((2026, 9, 11, 14, 3, 27, 0, 0, -1)) + 0.415
    assert format_log_time(ts) == "14:03:27.415"


def test_milliseconds_are_zero_padded():
    ts = time.mktime((2026, 9, 11, 9, 5, 7, 0, 0, -1)) + 0.007
    assert format_log_time(ts) == "09:05:07.007"


def test_format_shape():
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}\.\d{3}", format_log_time(time.time()))


def test_rounding_carries_into_seconds():
    """.9996 с округляется до следующей секунды, а не в «.1000»."""
    ts = time.mktime((2026, 9, 11, 14, 3, 27, 0, 0, -1)) + 0.9996
    assert format_log_time(ts) == "14:03:28.000"
