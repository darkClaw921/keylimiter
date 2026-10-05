"""Тесты разбора и валидации config.json."""

import json

import pytest

from keylimiter import config as config_module
from keylimiter.config import Config, Rule
from keylimiter.scheduler import AutoEnterSettings


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "config_path", lambda: path)
    return path


def test_creates_default_when_missing(cfg_file):
    cfg = config_module.load()
    assert cfg_file.exists()
    assert [r.key for r in cfg.rules] == ["ENTER"]
    assert cfg.rules[0].limit == 6
    assert cfg.rules[0].window_ms == 1000
    assert cfg.rules[0].block_ms == 1000


def test_roundtrip(cfg_file):
    original = Config(rules=[Rule(key="SPACE", limit=3, window_ms=500, block_ms=2000)])
    config_module.save(original)
    loaded = config_module.load()
    assert loaded.rules[0] == original.rules[0]


def test_invalid_rules_are_dropped(cfg_file):
    cfg_file.write_text(json.dumps({"rules": [
        {"key": "ENTER", "limit": 4},
        {"key": "НЕТТАКОЙ", "limit": 4},
        {"key": "SPACE", "limit": 0},
        {"key": "TAB", "window_ms": "быстро"},
        "мусор",
    ]}), encoding="utf-8")
    cfg = config_module.load()
    assert [r.key for r in cfg.rules] == ["ENTER"]
    assert cfg.rules[0].limit == 4


def test_falls_back_when_no_valid_rules(cfg_file):
    cfg_file.write_text(json.dumps({"rules": [{"key": "НЕТТАКОЙ"}]}), encoding="utf-8")
    cfg = config_module.load()
    assert [r.key for r in cfg.rules] == ["ENTER"]


def test_broken_json_does_not_crash(cfg_file):
    cfg_file.write_text("{это не json", encoding="utf-8")
    cfg = config_module.load()
    assert cfg.rules[0].key == "ENTER"


def test_key_name_is_normalized(cfg_file):
    cfg_file.write_text(json.dumps({"rules": [{"key": " enter "}]}), encoding="utf-8")
    cfg = config_module.load()
    assert cfg.rules[0].key == "ENTER"
    assert cfg.rules[0].vk == 0x0D


def test_auto_enter_settings_roundtrip(cfg_file):
    original = Config(auto_enter=AutoEnterSettings(time="23:59:58", count=12, interval_ms=150,
                                                 timezone="Asia/Tokyo"))
    config_module.save(original)
    assert config_module.load().auto_enter == original.auto_enter


def test_old_config_gets_default_auto_enter_settings(cfg_file):
    cfg_file.write_text(json.dumps({"rules": [{"key": "SPACE"}]}), encoding="utf-8")
    cfg = config_module.load()
    assert cfg.auto_enter == AutoEnterSettings()
    assert cfg.rules[0].key == "SPACE"


@pytest.mark.parametrize("value", [
    None, [], "invalid", {"time": "25:00"}, {"time": None},
    {"count": 0}, {"count": True}, {"count": "5"}, {"interval_ms": -1},
])
def test_invalid_auto_enter_settings_do_not_break_rules(cfg_file, value, caplog):
    cfg_file.write_text(json.dumps({"rules": [{"key": "SPACE"}], "auto_enter": value}),
                        encoding="utf-8")
    cfg = config_module.load()
    assert cfg.auto_enter == AutoEnterSettings()
    assert cfg.rules[0].key == "SPACE"
    assert "Некорректные настройки автоматического Enter" in caplog.text


def test_existing_auto_enter_settings_without_timezone_default_to_moscow(cfg_file):
    cfg_file.write_text(json.dumps({"auto_enter": {"time": "16:45:29", "count": 8,
                                                  "interval_ms": 150}}), encoding="utf-8")
    settings = config_module.load().auto_enter
    assert settings == AutoEnterSettings(time="16:45:29", count=8, interval_ms=150,
                                         timezone="Europe/Moscow")


def test_invalid_timezone_is_logged_and_does_not_break_rules(cfg_file, caplog):
    cfg_file.write_text(json.dumps({"rules": [{"key": "SPACE"}],
                                    "auto_enter": {"timezone": "Bad/Timezone"}}), encoding="utf-8")
    cfg = config_module.load()
    assert cfg.auto_enter.timezone == "Europe/Moscow"
    assert cfg.rules[0].key == "SPACE"
    assert "Неизвестный часовой пояс" in caplog.text


def test_multiple_timers_roundtrip(cfg_file):
    timers = [AutoEnterSettings(time="09:05:37", count=5),
              AutoEnterSettings(time="10:11:12", timezone="Asia/Tokyo", count=8, interval_ms=50)]
    config_module.save(Config(timers=timers))
    assert config_module.load().timers == timers


def test_legacy_single_timer_migrated(cfg_file):
    cfg_file.write_text(json.dumps({"auto_enter": {"time": "16:45:29", "count": 8}}),
                        encoding="utf-8")
    cfg = config_module.load()
    assert cfg.timers == [AutoEnterSettings(time="16:45:29", count=8)]
    assert cfg.timers[0] is not cfg.auto_enter
    config_module.save(cfg)
    assert config_module.load().timers == cfg.timers


def test_invalid_timer_does_not_drop_valid_timers(cfg_file, caplog):
    cfg_file.write_text(json.dumps({"timers": [
        {"time": "09:00:00", "count": 3}, {"time": "bad"}, None,
        {"time": "10:00:00", "timezone": "UTC"},
    ]}), encoding="utf-8")
    cfg = config_module.load()
    assert cfg.timers == [AutoEnterSettings(time="09:00:00", count=3),
                          AutoEnterSettings(time="10:00:00", timezone="UTC")]
    assert "Таймер №2 пропущен" in caplog.text
    assert "Таймер №3 пропущен" in caplog.text


def test_empty_timer_list_preserved_without_legacy_fallback(cfg_file):
    config_module.save(Config(auto_enter=AutoEnterSettings(count=10), timers=[]))
    assert config_module.load().timers == []


@pytest.mark.parametrize("timers", [None, {}, "bad"])
def test_broken_timer_list_does_not_crash(cfg_file, timers, caplog):
    cfg_file.write_text(json.dumps({"timers": timers}), encoding="utf-8")
    assert config_module.load().timers == []
    assert "Список таймеров повреждён" in caplog.text
