#!/usr/bin/env bash
# 打包「佳明健康助手」为单文件 exe（Windows）
# 用法： bash build_exe.sh
set -e

PYI="/c/Users/user/.workbuddy/binaries/python/envs/default/Scripts/pyinstaller.exe"
ROOT="/c/Users/user/WorkBuddy/garmin-health-assistant"
cd "$ROOT"

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
