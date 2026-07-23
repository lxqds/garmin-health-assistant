"""agent/core.py —— Agent 主循环（LLM + function-calling 工具调度）

流程：
  1. 拼装 system + 历史 + 用户消息
  2. 调 chat_with_tools；若 LLM 返回 tool_calls，则逐个执行工具、把结果回灌
  3. 最多迭代若干轮，直到 LLM 给出最终自然语言回复
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from agent import llm

SYSTEM_PROMPT = """你是「佳明健康助手」内置的 AI Agent，运行在用户桌面应用中。
你掌握用户的佳明（Garmin）健康与训练数据，能帮用户查询、分析、并主动执行操作。

你可以使用以下工具：
- query_health(date)：查某天健康日报（状态/指标/建议）
- query_plan(which)：查今日/明日训练计划（Garmin Coach）
- push_feishu(content) / push_wx(content)：把内容推送到飞书群 / 微信
- trigger_sync()：触发一次佳明数据同步
- get_config() / set_config(key,value)：查看 / 修改应用配置（如帮用户填写 API Key）
- set_reminder(text,when)：设置本地提醒

行为准则：
1. 优先用工具获取真实数据再回答，不要凭空编造健康数值。
2. 用户要你「发到飞书/微信」「同步数据」「改配置」「设提醒」时，直接调用对应工具并执行，执行后简要反馈结果。
3. 涉及写操作（推送/同步/改配置/提醒）前，若信息不足可先向用户确认，但用户明确指示就果断执行。
4. 用简洁、口语化、中文回复；健康建议要稳妥，必要时提示「以专业意见为准」。
5. 当用户要填 API 时，引导用户提供 key，并用 set_config 写入对应键。
"""


class Agent:
    def __init__(self, provider_cfg: Optional[dict] = None,
                 tools: Optional[Dict] = None, max_steps: int = 6):
        self.provider_cfg = provider_cfg or {}
        self.tools = tools if tools is not None else {}
        self.max_steps = max_steps
        self.system = SYSTEM_PROMPT

    def run(self, user_msg: str, history: Optional[List[dict]] = None) -> str:
        """执行一轮对话，返回最终自然语言回复。

        history: 之前的 [{role, content}] 列表（不含 system）。
        """
        messages = [{"role": "system", "content": self.system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_msg})

        schemas = [t.schema() for t in self.tools.values()] if self.tools else None

        for step in range(self.max_steps):
            if schemas:
                msg = llm.chat_with_tools(messages, self.provider_cfg, schemas)
            else:
                # 没有工具时退化为普通对话
                content = llm.chat(messages, self.provider_cfg)
                return content
            # 无工具调用 -> 直接给最终回复
            if not getattr(msg, "tool_calls", None):
                return msg.content or ""
            # 把 assistant（带 tool_calls）加回消息
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
            # 逐个执行工具
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                tool = self.tools.get(fn_name)
                if tool is None:
                    result = f"未知工具：{fn_name}"
                else:
                    try:
                        result = str(tool(**args))
                    except Exception as e:
                        result = f"工具执行出错：{e}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        # 达到最大步数，做最后一次总结性回复
        return llm.chat(messages, self.provider_cfg)
