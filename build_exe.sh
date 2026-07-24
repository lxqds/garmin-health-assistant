#!/usr/bin/env bash
# 打包「佳明健康助手」为单文件 exe（Windows）
# 用法：
#   pip install -r requirements.txt      # 含 pyinstaller / PyQt6 / PyQt6-WebEngine
#   bash build_exe.sh
set -e

# 仓库根目录（脚本所在目录，避免硬编码路径）
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# 解析 pyinstaller：优先用 PATH 中的，回退到本机 managed venv
PYI="$(command -v pyinstaller 2>/dev/null || command -v pyinstaller.exe 2>/dev/null || true)"
if [ -z "$PYI" ]; then
  PYI="/c/Users/user/.workbuddy/binaries/python/envs/default/Scripts/pyinstaller.exe"
fi
echo "使用 pyinstaller: $PYI"

"$PYI" --noconfirm --onefile --windowed \
  --name GarminHealthAssistant \
  --icon "assets/icon.ico" \
  --add-data "dashboard/templates;dashboard/templates" \
  --add-data "assets;assets" \
  --hidden-import PyQt6.QtWebEngineWidgets \
  --hidden-import flask \
  --hidden-import garminconnect \
  --hidden-import markdown \
  --hidden-import dotenv \
  --hidden-import openai \
  --hidden-import ai.ai_analyze \
  --hidden-import ai.snapshot \
  --hidden-import ai.prompts \
  --hidden-import ai.coach_plan \
  --paths "$ROOT" \
  run_app.py

echo "BUILD DONE -> $ROOT/dist/GarminHealthAssistant.exe"
