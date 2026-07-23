"""agent —— 内置轻量 agent 框架（LLM + 工具循环）

等价于 WorkBuddy 的 agent 思路，但自包含、可随 EXE 分发：
  - LLM 走 OpenAI 兼容接口（默认 DeepSeek，UI 可切换 base_url/key/model）
  - 工具以支持 function-calling 的 schema 注册，Agent 自行决定调用
  - 工具覆盖：查佳明健康/计划、发飞书/微信、触发同步、读写配置、设提醒
"""
from agent.core import Agent
from agent.tools import build_tools

__all__ = ["Agent", "build_tools"]
