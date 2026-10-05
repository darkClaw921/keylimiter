# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: один exe без консольного окна.

UPX намеренно отключён — сжатый бинарник с клавиатурным хуком куда чаще
получает ложное срабатывание антивируса.
"""

from PyInstaller.utils.hooks import collect_all

tzdata_datas, tzdata_binaries, tzdata_imports = collect_all('tzdata')

a = Analysis(
    ['__main__.py'],
    pathex=[],
    binaries=tzdata_binaries,
    datas=tzdata_datas,
    hiddenimports=['pystray._win32', 'PIL.Image', 'PIL.ImageDraw'] + tzdata_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='KeyLimiter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
