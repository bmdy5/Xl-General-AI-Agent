# -*- coding: utf-8 -*-
"""通用视觉 Agent 骨架。

零平台特定代码。不硬编码任何 CSS、坐标、用户名。
所有操作知识来自记忆系统，每次执行自动积累经验。

依赖: 视觉网关 (/vision/*) + 记忆系统 + LLM

配置 (环境变量，有默认值):
  VISUAL_AGENT_MODEL      — 决策模型 (默认 deepseek/deepseek-v4-flash)
  VISUAL_AGENT_MAX_STEPS  — 最大步数 (默认 5)
"""

import json
import hashlib
import logging
import os
import random
import asyncio

logger = logging.getLogger("agent.visual")

from agent.tools.visual_tools import call_vision_gateway

# ── 配置 (换模型只改这里) ──
DEFAULT_MODEL = os.getenv("VISUAL_AGENT_MODEL", "deepseek/deepseek-v4-flash")
DEFAULT_MAX_STEPS = int(os.getenv("VISUAL_AGENT_MAX_STEPS", "5"))

DECIDE_PROMPT = """你是小萤的视觉操作引擎。你只能通过执行给定的操作来完成任务。

任务: {task}
已执行步骤: {history_text}
相关记忆: {memories}

可用操作:
- click: 点击坐标(x,y)
- type: 输入文本text
- scroll: 滚动屏幕，direction=up/down，amount=像素(默认400)
- wait: 等待seconds秒(默认2)
- done: 任务完成

规则:
1. 输出严格JSON，包含thought字段
2. 坐标必须来自记忆或经验，不能编造
3. 连续两次操作无效果 → 输出done
4. 不确定时选保守操作

输出示例:
{{"thought": "搜索框在页面顶部，点击激活", "action": "click", "x": 500, "y": 100}}"""


class VisualAgent:
    """通用视觉操作骨架。

    agent = VisualAgent(
        gateway_port=9000,
        llm_client=llm,
        memory_manager=memory,
        model="deepseek/deepseek-v4-flash",   # 可选，换决策模型
        max_steps=5,                            # 可选，最大步数
    )
    result = await agent.execute(task="回复抖音私信")
    """

    def __init__(self, gateway_port: int, llm_client, memory_manager,
                 model: str = "", max_steps: int = 0):
        self.port = gateway_port
        self.llm = llm_client
        self.memory = memory_manager
        self.model = model or DEFAULT_MODEL
        self.max_steps = max_steps or DEFAULT_MAX_STEPS
        self.running = True

    def stop(self):
        """紧急停止。"""
        self.running = False

    async def execute(self, task: str, context: str = "") -> dict:
        """执行视觉任务。返回 {success, steps, history, error}。"""
        self.running = True
        history: list[dict] = []
        last_action = None
        fail_count = 0

        for step in range(self.max_steps):
            if not self.running:
                return {"success": False, "error": "手动停止", "steps": step, "history": history}

            # 1. 查记忆 (成功经验优先)
            memories = await self._recall(task, history)

            # 2. LLM 决策 (纯文本，零图片 token)
            history_text = "\n".join(
                [f"Step{i}: {h['action']} → {h.get('result','')}" for i, h in enumerate(history)]
            ) or "无"
            prompt = DECIDE_PROMPT.format(
                task=task,
                history_text=history_text,
                memories=memories or "无相关记忆",
            )

            resp = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model_override=self.model,
            )
            try:
                action = json.loads(resp.get("content", "{}"))
            except json.JSONDecodeError:
                action = {"action": "done", "thought": "JSON解析失败"}

            logger.info(f"[Vis S{step}] {action.get('thought','')[:80]} → {action.get('action')}")

            if action.get("action") == "done":
                await self._remember(task, action, "任务完成", step, True)
                return {"success": True, "steps": step + 1, "history": history}

            # 3. 执行前截图哈希
            before_hash = await self._screenshot_hash()

            # 4. 执行
            result = await self._act(action)
            history.append({"step": step, "action": action.get("action"), "result": result})

            # 5. 比对变化
            await asyncio.sleep(1.0)
            after_hash = await self._screenshot_hash()
            changed = before_hash != after_hash

            # 6. 失败检测: 同操作+无变化=失败累积
            if action.get("action") == last_action and not changed:
                fail_count += 1
            else:
                fail_count = 0
            last_action = action.get("action")

            if fail_count >= 2:
                await self._remember(task, action, f"连续{fail_count}次无变化", step, False)
                return {"success": False, "error": f"连续{fail_count}次无变化", "steps": step + 1, "history": history}

        return {"success": False, "error": "超过最大步数", "steps": self.max_steps, "history": history}

    # ── 内部 ──

    async def _screenshot_hash(self) -> str:
        b64 = await self._screenshot()
        return hashlib.md5(b64.encode()).hexdigest() if b64 else ""

    async def _screenshot(self) -> str:
        try:
            return await call_vision_gateway(self.port, "screenshot", {})
        except Exception:
            return ""

    async def _recall(self, task: str, history: list) -> str | None:
        try:
            actions = ",".join([h.get("action", "") for h in history[-2:]])
            results = await self.memory.search_memories(f"{task} {actions}", limit=5)
            if not results:
                return None
            success, failure = [], []
            for r in results:
                content = r.get("content", "")[:250]
                if "visual_success" in r.get("filename", ""):
                    success.append(content)
                else:
                    failure.append(f"⚠️失败: {content[:150]}")
            return "\n".join(success + failure)
        except Exception:
            return None

    async def _act(self, action: dict) -> str:
        await asyncio.sleep(random.uniform(0.1, 0.3))
        a = action.get("action", "")
        try:
            if a == "click":
                x, y = int(action["x"]), int(action["y"])
                await call_vision_gateway(self.port, "click", {"x": x, "y": y})
                return f"click({x},{y})"
            elif a == "type":
                text = str(action.get("text", ""))
                await call_vision_gateway(self.port, "type", {"text": text})
                return f"type({text[:30]})"
            elif a == "scroll":
                d = action.get("direction", "down")
                amt = int(action.get("amount", 400))
                await call_vision_gateway(self.port, "scroll", {"direction": d, "amount": amt})
                return f"scroll({d},{amt})"
            elif a == "wait":
                s = float(action.get("seconds", 2))
                await asyncio.sleep(s)
                return f"wait({s}s)"
            return f"unknown:{a}"
        except Exception as e:
            return f"error:{e}"

    async def _remember(self, task: str, action: dict, result: str, steps: int, success: bool) -> None:
        try:
            tag = "success" if success else "failure"
            await self.memory.save(
                filename=f"visual_{tag}_{action.get('action','unknown')}",
                description=f"[视觉{tag}] {action.get('thought','')[:80]}",
                content=json.dumps({
                    "task": task, "action": action, "result": result,
                    "steps": steps, "success": success,
                }, ensure_ascii=False),
            )
        except Exception as e:
            logger.debug(f"记忆写入失败: {e}")
