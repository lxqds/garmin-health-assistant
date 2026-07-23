#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_app.py —— 启动「佳明健康助手」桌面应用（EXE 入口）

双击 / PyInstaller 打包后，本文件即程序入口：
  1. 后台起 Flask 服务（仪表盘 + agent API）
  2. 起 PyQt6 窗口（WebView 嵌仪表盘 + 右侧 AI 聊天 + 设置菜单）

开发期运行：  python run_app.py
"""
from desktop.main import launch

if __name__ == "__main__":
    launch()
