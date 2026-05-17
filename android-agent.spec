# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ['src/agent/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src/agent/config/default.yaml', 'agent/config/'),
        ('.env.example', '.'),
    ],
    hiddenimports=[
        'faster_whisper',
        'sounddevice',
        'numpy',
        'websockets',
        'yaml',
        'pydantic',
        'pydantic_settings',
        'agent.config',
        'agent.config.config',
    ] + collect_submodules('ctranslate2'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'pandas',
        'scipy',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='android-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
