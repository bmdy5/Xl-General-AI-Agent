"""内置文件工具：read_file.

遵循 ACI 原则：
- 强制绝对路径（Poka-yoke）
- 文件大小限制
- start_line/end_line 行号切片
- LRU 防抖缓存（60s内同文件同大小拦截）
"""

import os
import time
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult

MAX_READ_SIZE = 30720  # 30KB


async def auto_maintain_note(file_path: str, content: str, context: Any):
    """当读取 markdown 学习笔记时，在后台自动使用 LLM 对其进行增量维护。"""
    if not context or not hasattr(context, "llm") or not context.llm:
        return

    latest_query = ""
    if hasattr(context, "messages") and context.messages:
        # 获取最新的几条用户消息作为维护上下文
        user_msgs = [m["content"] for m in context.messages if m.get("role") == "user"]
        if user_msgs:
            latest_query = user_msgs[-1]

    prompt = f"""你是一个专业的 Markdown 笔记排版与知识整理专家。
现在用户正在阅读这篇笔记。请结合当前最新的上下文/讨论情况："{latest_query}"，对这篇笔记进行智能维护和优化：
1. 增量更新：如果当前最新的讨论/情况中包含新的结论、踩坑记录或技术点，请将其以简洁明了的方式增量合并/补充到笔记对应章节中。
2. 规范格式：确保包含或补充完整的 YAML Frontmatter (title, date, tags)。
3. 优化排版：规范层级标题，加粗关键词/专业术语，修复排版混乱或代码块格式。
4. 绝对原则：绝不能删减原有正文的核心意思，不要随意删减已有的代码块和宝贵记录！

以下是需要维护的笔记原文：
---
{content}
---

请只输出更新维护后的完整 Markdown 文本。不要包含任何思考过程或寒暄，也不要在最外层包裹 ```markdown 和 ``` 标记符。"""

    try:
        if len(content) > 15000:
            return

        res = await context.llm.chat(messages=[{"role": "user", "content": prompt}])
        new_content = res.get("content", "").strip()

        if not new_content or new_content == content:
            return

        if new_content.startswith("```markdown"):
            new_content = new_content[len("```markdown"):].lstrip()
        elif new_content.startswith("```md"):
            new_content = new_content[len("```md"):].lstrip()
        elif new_content.startswith("```"):
            new_content = new_content[len("```"):].lstrip()

        if new_content.endswith("```"):
            new_content = new_content[:-3].rstrip()

        # 备份并覆写
        path = Path(file_path)
        backup_path = path.with_suffix('.md.bak')
        shutil.copy2(path, backup_path)
        path.write_text(new_content, encoding='utf-8')
        logging.info(f"[笔记自维护] 成功维护并更新笔记: {path.name} (已备份至 {backup_path.name})")
    except Exception as e:
        logging.error(f"[笔记自维护] 自动更新笔记时发生错误: {e}")


class ReadFileTool(BaseTool):
    def __init__(self):
        self._read_cache: dict[str, tuple[float, int]] = {}  # path → (timestamp, size)

    @property
    def name(self) -> str:
        return "read_file"

    async def description(self) -> str:
        return "读取指定绝对路径的文件内容。"

    def is_read_only(self) -> bool:
        return True

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "读取本地文件系统中的文件。"
                    "file_path 必须是绝对路径。"
                    f"如果文件大于 {MAX_READ_SIZE // 1024}KB，将被截断。"
                    "对于大文件，请使用 start_line/end_line 参数分段读取指定的行号范围。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "要读取文件的绝对路径。",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "可选：开始读取的行号（从 1 开始计）。常用于分段读取大文件。",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "可选：结束读取的行号（包含该行）。搭配 start_line 使用。",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        file_path = input_args.get("file_path", "")
        if not file_path:
            return {"result": False, "message": "file_path is required"}
        if not Path(file_path).is_absolute():
            return {"result": False, "message": "file_path must be absolute, e.g. /home/user/file.txt"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        file_path = input_args["file_path"]
        start = input_args.get("start_line")
        end = input_args.get("end_line")
        path = Path(file_path)

        # ── 行号参数校验 ──
        if start is not None and (not isinstance(start, int) or start < 1):
            yield ToolResult(type="result", data="Error: start_line must be a positive integer")
            return
        if end is not None and (not isinstance(end, int) or end < 1):
            yield ToolResult(type="result", data="Error: end_line must be a positive integer")
            return
        if start is not None and end is not None and start > end:
            yield ToolResult(type="result", data=f"Error: start_line ({start}) must be <= end_line ({end})")
            return

        if not path.exists():
            yield ToolResult(
                type="result",
                data=(
                    f"Error: file not found: {file_path}. "
                    f"[行为纠正]: 禁止盲猜路径！请先使用 bash 工具执行 find 或 ls "
                    f"命令获取准确的目录结构！"
                ),
            )
            return

        if path.is_dir():
            try:
                # 使用 iterdir 代替模糊 glob
                items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
                lines = [
                    f"[系统自愈引导]: 目标路径 {file_path} 是一个目录，不能直接用 read_file 读取。已自动为你列出该目录下的第一层结构：\n"
                ]
                for item in items[:50]:
                    suffix = "/" if item.is_dir() else ""
                    size_str = f" ({item.stat().st_size} bytes)" if item.is_file() else ""
                    lines.append(f"- {item.name}{suffix}{size_str}")
                if len(items) > 50:
                    lines.append(f"... (共 {len(items)} 个项目，已截断显示)")
                lines.append("\n[建议]: 请根据以上目录结构，选择正确的具体文件路径，再重新调用 read_file 读取其内容。")
                yield ToolResult(type="result", data="\n".join(lines))
            except Exception as e:
                yield ToolResult(type="result", data=f"Error: path is a directory: {file_path}. 列出目录结构失败: {e}")
            return

        try:
            size = path.stat().st_size

            # ── LRU 防抖：60s内同路径同大小同行号范围 → 拦截 ──
            cache_key = f"{file_path}:{start}:{end}"
            now = time.time()
            cached = self._read_cache.get(cache_key)
            if cached:
                cached_ts, cached_size = cached
                if now - cached_ts < 60 and cached_size == size:
                    yield ToolResult(
                        type="result",
                        data=(
                            f"[系统拦截]: 警告！你在过去的 60 秒内已经读取过 {file_path} "
                            f"({size} bytes)，内容未变化。请直接查阅上文对话记忆中的文件内容，"
                            f"严禁重复无意义的读取消耗 Token！"
                        ),
                    )
                    return

            # 更新缓存
            self._read_cache[cache_key] = (now, size)
            if len(self._read_cache) > 20:
                oldest = min(self._read_cache, key=lambda k: self._read_cache[k][0])
                del self._read_cache[oldest]

            # ── 读取（支持行号切片）──
            lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
            full_content = "\n".join(lines)

            # 判断是否是学习笔记，如果是，则在后台异步触发自维护更新
            if path.suffix == ".md" and ("学习笔记" in file_path or "documents/个人博客/学习笔记" in file_path):
                asyncio.create_task(auto_maintain_note(file_path, full_content, context))

            if start is not None or end is not None:
                s = start or 1
                e = end if end is not None else len(lines)
                content = "\n".join(lines[s - 1:e])
                yield ToolResult(
                    type="result",
                    data=f"Lines {s}-{e} of {file_path} ({len(lines)} lines total):\n{content}",
                )
                return

            content = "\n".join(lines)
            if len(content) > MAX_READ_SIZE:
                content = content[:MAX_READ_SIZE] + f"\n\n... (truncated, total {size} bytes)"

            yield ToolResult(type="result", data=content)
        except UnicodeDecodeError:
            yield ToolResult(type="result", data=f"Error: binary file, cannot read as text: {file_path}")
        except PermissionError:
            yield ToolResult(type="result", data=f"Error: permission denied: {file_path}")
        except Exception as e:
            yield ToolResult(type="result", data=f"Error reading file: {e}")
