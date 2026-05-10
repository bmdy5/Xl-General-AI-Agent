"""Session persistence + cross-session search — adapted from tinypace + hermes FTS5.

JSONL append-only + os.fsync crash-safe.
Cross-session: grep all JSONL files, LLM summarize matches.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiofiles

logger = logging.getLogger(__name__)


class SessionHandler:
    """会话 JSONL 持久化管理器.

    用法：
        handler = SessionHandler("session-001")
        await handler.initialize()
        await handler.append_message({"role": "user", "content": "hello"})
        messages = await handler.load_messages()
    """

    def __init__(self, session_id: str, storage_dir: Optional[str] = None):
        self.session_id = session_id

        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path.home() / ".my-agent" / "sessions"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.storage_dir / f"{session_id}.jsonl"

    async def initialize(self) -> list[dict]:
        """初始化（备份旧文件 + 加载消息）."""
        self._backup()
        return await self.load_messages()

    def _backup(self):
        """如果会话文件存在，创建 .bak 备份."""
        if not self.session_file.exists():
            return
        bak = self.session_file.with_suffix(".jsonl.bak")
        bak.write_bytes(self.session_file.read_bytes())

    async def load_messages(self) -> list[dict]:
        """从 JSONL 文件加载所有消息，自动修复孤儿 tool_calls."""
        if not self.session_file.exists():
            return []

        messages = []
        async with aiofiles.open(self.session_file, mode="r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # 修复孤儿 tool_calls（抄 openclaw transcript repair）
        return self._repair_orphan_tool_calls(messages)

    def _repair_orphan_tool_calls(self, messages: list[dict]) -> list[dict]:
        """扫描并修复孤儿 tool_calls/tool 消息.

        DeepSeek 要求：每个 assistant tool_call 必须有对应的 tool 消息，
        每个 tool 消息必须有对应的 assistant tool_call。
        缺任何一边都会报错。
        """
        # 1. 收集 assistant→tool_call 的映射
        assistant_tc_ids: set[str] = set()
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if tc.get("id"):
                        assistant_tc_ids.add(tc["id"])

        # 2. 收集 tool→tool_call_id 的映射
        tool_result_ids: set[str] = set()
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id"):
                tool_result_ids.add(m["tool_call_id"])

        # 3. 只保留双方都存在的配对
        valid_ids = assistant_tc_ids & tool_result_ids

        repaired = []
        removed = 0
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                valid_calls = [
                    tc for tc in m["tool_calls"]
                    if tc.get("id") in valid_ids
                ]
                if valid_calls:
                    repaired.append({**m, "tool_calls": valid_calls})
                elif m.get("content"):
                    # 有文本内容 → 保留文本，去掉无效 tool_calls
                    cleaned = {k: v for k, v in m.items() if k != "tool_calls"}
                    repaired.append(cleaned)
                else:
                    # 只有无效 tool_calls，没有文本 → 整条移除
                    removed += 1
                continue

            if m.get("role") == "tool":
                if m.get("tool_call_id") in valid_ids:
                    repaired.append(m)
                else:
                    removed += 1
                continue

            repaired.append(m)

        if removed > 0:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Transcript repair: removed {removed} orphan messages "
                f"({len(messages)} → {len(repaired)} total)"
            )

        return repaired

    async def replace_all(self, messages: list[dict]) -> None:
        """压缩后重写整个会话文件，清除已被压缩的旧消息."""
        async with aiofiles.open(self.session_file, mode="w", encoding="utf-8") as f:
            for msg in messages:
                await f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            await f.flush()
            loop = __import__("asyncio").get_running_loop()
            await loop.run_in_executor(None, os.fsync, f.fileno())

    async def append_message(self, message: dict) -> None:
        """追加一条消息到 JSONL 文件末尾."""
        async with aiofiles.open(self.session_file, mode="a", encoding="utf-8") as f:
            await f.write(json.dumps(message, ensure_ascii=False) + "\n")
            await f.flush()
            loop = __import__("asyncio").get_running_loop()
            await loop.run_in_executor(None, os.fsync, f.fileno())

    async def search_all_sessions(self, query: str, llm, max_results: int = 5) -> str:
        """跨会话搜索：grep所有JSONL → LLM摘要（抄 hermes FTS5 模式）."""
        if not self.storage_dir.exists():
            return ""

        matches = []
        for f in sorted(self.storage_dir.glob("*.jsonl"), reverse=True)[:20]:
            if f.name == self.session_file.name:
                continue
            try:
                async with aiofiles.open(f, mode="r", encoding="utf-8") as fh:
                    content = await fh.read()
            except Exception:
                continue
            # 简单关键词匹配
            keywords = query.lower().split()
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if any(kw in line.lower() for kw in keywords):
                    try:
                        msg = json.loads(line)
                        text = str(msg.get("content", ""))[:300]
                        if text:
                            matches.append(f"[{f.stem}:{i}] {text}")
                    except json.JSONDecodeError:
                        continue
            if len(matches) >= 20:
                break

        if not matches:
            return "No past conversations found."

        # LLM 摘要匹配结果
        try:
            response = await llm.chat(
                messages=[{"role": "user", "content": (
                    f"搜索'{query}'，找到以下历史对话片段。请用3-5句话总结相关内容:\n"
                    + "\n".join(matches[:max_results * 3])
                )}],
                tools=None,
            )
            return response.get("content", "\n".join(matches[:max_results]))
        except Exception:
            return "\n".join(matches[:max_results])
