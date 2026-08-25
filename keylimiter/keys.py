"""Таблица virtual-key кодов Windows и человекочитаемых имён."""

# Имя -> VK код. Имена регистронезависимы при разборе.
VK_BY_NAME: dict[str, int] = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "SHIFT": 0x10,
    "CTRL": 0x11,
    "ALT": 0x12,
    "PAUSE": 0x13,
    "CAPSLOCK": 0x14,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PRINTSCREEN": 0x2C,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "LWIN": 0x5B,
    "RWIN": 0x5C,
    "NUMPAD0": 0x60,
    "NUMPAD1": 0x61,
    "NUMPAD2": 0x62,
    "NUMPAD3": 0x63,
    "NUMPAD4": 0x64,
    "NUMPAD5": 0x65,
    "NUMPAD6": 0x66,
    "NUMPAD7": 0x67,
    "NUMPAD8": 0x68,
    "NUMPAD9": 0x69,
    "NUMPAD*": 0x6A,
    "NUMPAD+": 0x6B,
    "NUMPAD-": 0x6D,
    "NUMPAD.": 0x6E,
    "NUMPAD/": 0x6F,
    "NUMLOCK": 0x90,
    "SCROLLLOCK": 0x91,
    "LSHIFT": 0xA0,
    "RSHIFT": 0xA1,
    "LCTRL": 0xA2,
    "RCTRL": 0xA3,
    "LALT": 0xA4,
    "RALT": 0xA5,
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
}

# 0-9 и A-Z совпадают с ASCII-кодами
for _c in "0123456789":
    VK_BY_NAME[_c] = ord(_c)
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK_BY_NAME[_c] = ord(_c)
# F1..F24
for _i in range(1, 25):
    VK_BY_NAME[f"F{_i}"] = 0x6F + _i

NAME_BY_VK: dict[int, str] = {}
for _name, _vk in VK_BY_NAME.items():
    # Первое встреченное имя считаем каноническим (ENTER раньше, чем дубликаты)
    NAME_BY_VK.setdefault(_vk, _name)

ALL_KEY_NAMES: list[str] = sorted(VK_BY_NAME)


def vk_from_name(name: str) -> int | None:
    """VK код по имени клавиши; None, если имя неизвестно."""
    if not isinstance(name, str):
        return None
    return VK_BY_NAME.get(name.strip().upper())


def name_from_vk(vk: int) -> str:
    """Имя клавиши по VK коду; для неизвестных — 'VK_0x??'."""
    return NAME_BY_VK.get(vk, f"VK_0x{vk:02X}")
