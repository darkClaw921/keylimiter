"""Точка входа KeyLimiter."""

import argparse
import logging
import sys

from . import config as config_module
from .keys import vk_from_name


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="KeyLimiter",
        description="Ограничивает частоту нажатий выбранных клавиш в Windows.",
    )
    parser.add_argument("--headless", action="store_true",
                        help="запуститься сразу в трее, без открытого окна")
    return parser.parse_args(argv)


def _pause_hotkey_vk(cfg) -> int | None:
    """Из строки вида 'ctrl+alt+p' берём последнюю (не-модификаторную) клавишу."""
    parts = [p.strip() for p in cfg.pause_hotkey.split("+") if p.strip()]
    return vk_from_name(parts[-1]) if parts else None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if sys.platform != "win32":
        print("KeyLimiter работает только в Windows: используется хук WH_KEYBOARD_LL.",
              file=sys.stderr)
        return 1

    args = _parse_args(argv)
    cfg = config_module.load()

    from .gui import App
    from .hook import KeyboardHook

    hook = KeyboardHook(
        rules=cfg.rules,
        count_injected=cfg.count_injected,
        pause_hotkey_vk=_pause_hotkey_vk(cfg),
    )
    hook.set_paused(not cfg.enabled_on_start)
    hook.start()

    app = App(cfg, hook)
    try:
        app.run(start_hidden=args.headless)
    finally:
        hook.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
