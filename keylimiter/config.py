"""Загрузка и сохранение config.json рядом с exe (или с исходником при запуске из Python)."""

import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .keys import vk_from_name

log = logging.getLogger(__name__)

CONFIG_NAME = "config.json"


@dataclass
class Rule:
    key: str
    limit: int = 6
    window_ms: int = 1000
    block_ms: int = 1000
    enabled: bool = True

    @property
    def vk(self) -> int | None:
        return vk_from_name(self.key)


@dataclass
class Config:
    enabled_on_start: bool = True
    pause_hotkey: str = "ctrl+alt+p"
    count_injected: bool = True
    rules: list[Rule] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rules is None:
            self.rules = [Rule(key="ENTER")]


def config_path() -> Path:
    """Каталог рядом с exe при frozen-сборке, иначе корень проекта."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / CONFIG_NAME


def _parse_rule(raw: object) -> Rule | None:
    """Разбор одного правила; None при любой некорректности (с записью в лог)."""
    if not isinstance(raw, dict):
        log.warning("Правило пропущено: ожидался объект, получено %r", raw)
        return None

    key = raw.get("key")
    if vk_from_name(key) is None:
        log.warning("Правило пропущено: неизвестная клавиша %r", key)
        return None

    values: dict[str, int] = {}
    for field, default in (("limit", 6), ("window_ms", 1000), ("block_ms", 1000)):
        value = raw.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            log.warning("Правило %r пропущено: %s=%r должно быть целым >= 1", key, field, value)
            return None
        values[field] = value

    return Rule(
        key=str(key).strip().upper(),
        enabled=bool(raw.get("enabled", True)),
        **values,
    )


def load() -> Config:
    """Читает config.json; при отсутствии или поломке возвращает дефолт и пересоздаёт файл."""
    path = config_path()
    if not path.exists():
        cfg = Config()
        save(cfg)
        return cfg

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Не удалось прочитать %s (%s) — используются настройки по умолчанию", path, exc)
        return Config()

    if not isinstance(raw, dict):
        log.warning("%s повреждён — используются настройки по умолчанию", path)
        return Config()

    rules = [r for r in (_parse_rule(item) for item in raw.get("rules", [])) if r is not None]
    if not rules:
        log.warning("В конфиге нет корректных правил — добавлено правило по умолчанию (ENTER)")
        rules = [Rule(key="ENTER")]

    return Config(
        enabled_on_start=bool(raw.get("enabled_on_start", True)),
        pause_hotkey=str(raw.get("pause_hotkey", "ctrl+alt+p")),
        count_injected=bool(raw.get("count_injected", True)),
        rules=rules,
    )


def save(cfg: Config) -> None:
    path = config_path()
    data = asdict(cfg)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("Не удалось сохранить %s: %s", path, exc)
