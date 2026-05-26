# -*- coding: utf-8 -*-
"""通用视觉 Agent 骨架。

零平台特定代码。不依赖任何网关进程。
自己连 CDP 操作浏览器：截图 → 看图决策 → 点击/打字/滚动 → 验证。

配置 (.env):
  VISUAL_AGENT_MODEL       — 决策模型 (默认 openai/glm-4-flash)
  VISUAL_AGENT_VISION_MODEL — 视觉模型 (默认 openai/glm-4v-flash)
  VISUAL_AGENT_MAX_STEPS   — 最大步数 (默认 5)
  VISUAL_CDP_URL           — CDP 调试地址 (默认 http://127.0.0.1:9222)
"""

import json
import hashlib
import logging
import os
import random
import asyncio

from playwright.async_api import async_playwright

logger = logging.getLogger("agent.visual")

# ── 配置 ──
DEFAULT_MODEL = os.getenv("VISUAL_AGENT_MODEL", "openai/glm-4-flash")
DEFAULT_VISION_MODEL = os.getenv("VISUAL_AGENT_VISION_MODEL", "openai/mimo-v2.5")
DEFAULT_MAX_STEPS = int(os.getenv("VISUAL_AGENT_MAX_STEPS", "5"))
DEFAULT_CDP_URL = os.getenv("VISUAL_CDP_URL", "http://127.0.0.1:9222")

DECIDE_PROMPT = """你是小萤的视觉操作引擎。根据任务、历史、记忆决定下一步。

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
1. 严格输出JSON，包含thought字段
2. 坐标基于截图中看到的实际位置
3. 连续两次操作无效果 → 输出done

输出示例:
{{"thought": "搜索框在页面顶部，点击激活", "action": "click", "x": 500, "y": 100}}"""


class VisualAgent:
    """通用视觉操作骨架。自己连 CDP，不依赖网关。

    async with VisualAgent(llm_client=llm, memory_manager=mem) as agent:
        result = await agent.execute(task="打开百度搜索Python")
    """

    def __init__(self, llm_client, memory_manager,
                 model: str = "", vision_model: str = "", max_steps: int = 0,
                 cdp_url: str = ""):
        self.llm = llm_client
        self.memory = memory_manager
        self.model = model or DEFAULT_MODEL
        self.vision_model = vision_model or DEFAULT_VISION_MODEL
        self.max_steps = max_steps or DEFAULT_MAX_STEPS
        self.cdp_url = cdp_url or DEFAULT_CDP_URL
        self.running = True
        self._playwright = None
        self._browser = None
        self._page = None

    async def __aenter__(self):
        await self._connect()
        return self

    async def __aexit__(self, *args):
        await self._disconnect()

    async def _connect(self):
        """连接 CDP 浏览器。"""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
        ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        for p in ctx.pages:
            if p.url and p.url != "about:blank":
                self._page = p
                logger.info(f"复用页面: {p.url}")
                break
        if not self._page:
            self._page = await ctx.new_page()
            await self._page.set_viewport_size({"width": 1280, "height": 800})
            logger.info("创建新页面")

    async def _disconnect(self):
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._browser = None

    def stop(self):
        self.running = False

    # ── 浏览器操作 (直连，不走 HTTP) ──

    async def _screenshot(self) -> str:
        import base64
        import io
        from PIL import Image
        try:
            img_bytes = await self._page.screenshot(type="png")
            img = Image.open(io.BytesIO(img_bytes))
            scale = min(800 / max(img.width, img.height), 1.0)
            if scale < 1.0:
                img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format='PNG', optimize=True)
                img_bytes = buf.getvalue()
            return f"data:image/png;base64,{base64.b64encode(img_bytes).decode()}"
        except Exception:
            return ""

    async def _click(self, x: int, y: int):
        try:
            # 注入更明显的粉色爱心光标和波纹动画
            await self._page.evaluate("""([tx, ty]) => {
                let cursor = document.getElementById('myagent-pink-cursor');
                if (!cursor) {
                    cursor = document.createElement('div');
                    cursor.id = 'myagent-pink-cursor';
                    cursor.style.position = 'fixed';
                    cursor.style.width = '36px';
                    cursor.style.height = '36px';
                    cursor.style.zIndex = '999999';
                    cursor.style.pointerEvents = 'none';
                    cursor.style.transition = 'all 0.5s cubic-bezier(0.25, 0.8, 0.25, 1)';
                    cursor.style.transform = 'translate(-50%, -50%)';
                    cursor.innerHTML = `
                        <svg viewBox="0 0 24 24" width="36" height="36" fill="#FF1493" style="filter: drop-shadow(0px 4px 6px rgba(0,0,0,0.3));">
                            <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
                        </svg>
                    `;
                    document.body.appendChild(cursor);
                    cursor.style.left = '0px';
                    cursor.style.top = '0px';
                }
                
                cursor.style.left = tx + 'px';
                cursor.style.top = ty + 'px';
                
                setTimeout(() => {
                    const ripple = document.createElement('div');
                    ripple.style.position = 'fixed';
                    ripple.style.left = tx + 'px';
                    ripple.style.top = ty + 'px';
                    ripple.style.width = '20px';
                    ripple.style.height = '20px';
                    ripple.style.borderRadius = '50%';
                    ripple.style.border = '4px solid #FF1493';
                    ripple.style.backgroundColor = 'rgba(255, 20, 147, 0.4)';
                    ripple.style.transform = 'translate(-50%, -50%) scale(1)';
                    ripple.style.transition = 'all 0.4s ease-out';
                    ripple.style.pointerEvents = 'none';
                    ripple.style.zIndex = '999998';
                    document.body.appendChild(ripple);
                    
                    requestAnimationFrame(() => {
                        ripple.style.transform = 'translate(-50%, -50%) scale(5)';
                        ripple.style.opacity = '0';
                    });
                    
                    setTimeout(() => ripple.remove(), 400);
                }, 500);
            }""", [x, y])
            # 等待滑移和波纹动画
            await asyncio.sleep(0.6)
        except Exception as e:
            pass
            
        await self._page.mouse.click(x, y)
        await asyncio.sleep(0.3)
        
        try:
            # 点击后清理光标，以免阻挡视线或截图
            await self._page.evaluate("""() => {
                const cursor = document.getElementById('myagent-pink-cursor');
                if (cursor) cursor.remove();
            }""")
        except Exception:
            pass

    async def _type(self, text: str):
        await self._page.keyboard.type(text, delay=20)

    async def _scroll(self, direction: str, amount: int):
        delta = amount if direction == "down" else -amount
        await self._page.mouse.wheel(0, delta)

    # ── 主循环 ──

    async def execute(self, task: str) -> dict:
        self.running = True
        history: list[dict] = []
        last_action = None
        fail_count = 0

        for step in range(self.max_steps):
            if not self.running:
                return {"success": False, "error": "手动停止", "steps": step, "history": history}

            memories = await self._recall(task, history)

            history_text = "\n".join(
                [f"Step{i}: {h['action']} → {h.get('result','')}" for i, h in enumerate(history)]
            ) or "无"
            prompt = DECIDE_PROMPT.format(
                task=task, history_text=history_text,
                memories=memories or "无相关记忆",
            )

            if step == 0:
                b64 = await self._screenshot()
                resp = await self.llm.chat(
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": b64}},
                    ]}],
                    model_override=self.vision_model,
                )
            else:
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

            before_hash = await self._screenshot_hash()
            result = await self._act(action)
            history.append({"step": step, "action": action.get("action"), "result": result})

            await asyncio.sleep(1.0)
            after_hash = await self._screenshot_hash()
            changed = before_hash != after_hash

            if action.get("action") == last_action and not changed:
                fail_count += 1
            else:
                fail_count = 0
            last_action = action.get("action")

            if fail_count >= 2:
                await self._remember(task, action, f"连续{fail_count}次无变化", step, False)
                return {"success": False, "error": f"连续{fail_count}次无变化", "steps": step + 1, "history": history}

        return {"success": False, "error": "超过最大步数", "steps": self.max_steps, "history": history}

    async def _screenshot_hash(self) -> str:
        b64 = await self._screenshot()
        return hashlib.md5(b64.encode()).hexdigest() if b64 else ""

    async def _recall(self, task: str, history: list) -> str | None:
        try:
            actions = ",".join([h.get("action", "") for h in history[-2:]])
            results = await self.memory.search_memories(f"{task} {actions}", limit=5)
            if not results:
                return None
            success, failure = [], []
            for r in results:
                c = r.get("content", "")[:250]
                if "visual_success" in r.get("filename", ""):
                    success.append(c)
                else:
                    failure.append(f"⚠️失败: {c[:150]}")
            return "\n".join(success + failure)
        except Exception:
            return None

    async def _act(self, action: dict) -> str:
        await asyncio.sleep(random.uniform(0.1, 0.3))
        a = action.get("action", "")
        try:
            if a == "click":
                x, y = int(action["x"]), int(action["y"])
                await self._click(x, y)
                return f"click({x},{y})"
            elif a == "type":
                text = str(action.get("text", ""))
                await self._type(text)
                return f"type({text[:30]})"
            elif a == "scroll":
                d = action.get("direction", "down")
                amt = int(action.get("amount", 400))
                await self._scroll(d, amt)
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
