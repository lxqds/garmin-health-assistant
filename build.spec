# -*- mode: python ; coding: utf-8 -*-
# 佳明健康助手 桌面应用打包配置
# 用法： pyinstaller build.spec
# 推荐 --onedir：结构保持，.env / assistant-data 与 exe 同目录即可读取。
import os
from pathlib import Path

BASE = Path(SPEC).resolve().parent

a = Analysis(
    [str(BASE / "run_app.py")],
    pathex=[str(BASE)],
    binaries=[],
    datas=[
        (str(BASE / "dashboard" / "templates"), "dashboard/templates"),
        (str(BASE / "notify" / "templates"), "notify/templates"),
    ],
    hiddenimports=[
        "agent", "agent.core", "agent.tools", "agent.llm",
        "config",
        "dashboard", "notify", "server",
        "flask", "openai", "requests", "dotenv", "matplotlib",
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore", "PyQt6.QtWebChannel",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="佳明健康助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # 窗口应用，不弹黑框
    windowed=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="佳明健康助手",
)
