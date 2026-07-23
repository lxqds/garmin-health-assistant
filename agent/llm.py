"""agent/llm.py —— OpenAI 兼容 LLM 客户端

默认走 DeepSeek（https://api.deepseek.com/v1），但 base_url / api_key / model
均可由配置覆盖，因此也能接 GPT、任意 OpenAI 兼容服务，甚至本地 Ollama。
"""
from __future__ import annotations

import os
from typing import Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - 依赖缺失时给出清晰错误
    OpenAI = None


# 默认厂商预设：UI 下拉可直接选
PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "ollama": {
        "label": "本地 Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
    },
}


def resolve_provider_cfg(cfg: dict) -> dict:
    """把配置（可能只给了 provider 名）解析成完整 {base_url, api_key, model}。"""
    provider = (cfg.get("provider") or "deepseek").lower()
    preset = PROVIDER_PRESETS.get(provider, {})
    base_url = cfg.get("base_url") or preset.get("base_url") or "https://api.deepseek.com/v1"
    model = cfg.get("model") or preset.get("model") or "deepseek-chat"
    api_key = cfg.get("api_key") or ""
    return {"base_url": base_url, "api_key": api_key, "model": model}


def build_client(cfg: dict):
    if OpenAI is None:
        raise RuntimeError("未安装 openai 库，无法调用 LLM（pip install openai）")
    c = resolve_provider_cfg(cfg)
    return OpenAI(api_key=c["api_key"] or "EMPTY", base_url=c["base_url"])


def chat(messages: list, cfg: dict, temperature: float = 0.6) -> str:
    """普通对话（无工具）。"""
    c = resolve_provider_cfg(cfg)
    client = build_client(cfg)
    resp = client.chat.completions.create(
        model=c["model"], messages=messages, temperature=temperature
    )
    return resp.choices[0].message.content or ""


def chat_with_tools(messages: list, cfg: dict, tools: list, temperature: float = 0.6):
    """带 function-calling 的对话，返回原始 message 对象（可能含 tool_calls）。"""
    c = resolve_provider_cfg(cfg)
    client = build_client(cfg)
    resp = client.chat.completions.create(
        model=c["model"],
        messages=messages,
        tools=tools,
        temperature=temperature,
    )
    return resp.choices[0].message
