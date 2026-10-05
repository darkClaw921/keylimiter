"""Отправка пары Enter down/up через Windows SendInput."""

import ctypes
import sys

# Метка только для собственных событий: остальные макросы подчиняются лимитеру.
AUTO_ENTER_MARKER = 0x4B4C454E
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D

# Фиксированная ширина Windows-типов, в том числе при проверке на другой ОС.
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
                ("mouseData", DWORD), ("dwFlags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", DWORD), ("data", INPUT_UNION)]


class EnterSender:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Автоматический Enter работает только в Windows.")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._send_input = user32.SendInput
        self._send_input.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        self._send_input.restype = ctypes.c_uint

    def press_enter(self) -> None:
        inputs = (INPUT * 2)()
        for event in inputs:
            event.type = INPUT_KEYBOARD
            event.ki.wVk = VK_RETURN
            event.ki.dwExtraInfo = AUTO_ENTER_MARKER
        inputs[1].ki.dwFlags = KEYEVENTF_KEYUP
        sent = self._send_input(2, inputs, ctypes.sizeof(INPUT))
        if sent != 2:
            error = ctypes.get_last_error()
            # Если прошёл только KEYDOWN, пробуем отпустить клавишу.
            if sent == 1:
                self._send_input(1, ctypes.pointer(inputs[1]), ctypes.sizeof(INPUT))
            raise OSError(
                f"SendInput: отправлено событий {sent}/2, код ошибки {error}. "
                "Проверьте права доступа к активному окну."
            )
