"""Тесты разбора и валидации config.json."""

import json

import pytest

from keylimiter import config as config_module
from keylimiter.config import Config, Rule


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
