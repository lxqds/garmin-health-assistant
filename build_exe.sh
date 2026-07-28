#!/usr/bin/env bash
# 打包「佳明健康助手」为单文件 exe（Windows）
# 用法：
#   pip install -r requirements.txt      # 含 pyinstaller / PyQt6 / PyQt6-WebEngine
#   bash build_exe.sh
set -e

# 仓库根目录（脚本所在目录，避免硬编码路径）
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# 工作/输出目录放到系统临时目录：避免某些沙箱把 os.remove 强制走回收站
# （回收站不可用时构建会崩）。临时目录下的删除走原生实现，不受影响。
TMPW="${TEMP:-/tmp}/gha_build_work"
TMPD="${TEMP:-/tmp}/gha_build_dist"

# 解析 pyinstaller：优先用 PATH 中的，回退到本机 managed venv
PYI="$(command -v pyinstaller 2>/dev/null || command -v pyinstaller.exe 2>/dev/null || true)"
if [ -z "$PYI" ]; then
  PYI="/c/Users/user/.workbuddy/binaries/python/envs/default/Scripts/pyinstaller.exe"
fi
echo "使用 pyinstaller: $PYI"

"$PYI" --noconfirm --onefile --windowed \
  --name GarminHealthAssistant \
  --workpath "$TMPW" --distpath "$TMPD" \
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

echo "BUILD DONE -> $TMPD/GarminHealthAssistant.exe"

# 把成品 exe 拷回仓库 dist/（原生 cp，不触发沙箱回收站拦截）
mkdir -p "$ROOT/dist"
cp -f "$TMPD/GarminHealthAssistant.exe" "$ROOT/dist/" 2>/dev/null || \
  cp -f "$TMPD/GarminHealthAssistant.exe" "$ROOT/dist/GarminHealthAssistant.exe"
echo "COPIED -> $ROOT/dist/GarminHealthAssistant.exe"
