"""笔记自动整理工具."""
import asyncio
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult


class OrganizeNotesTool(BaseTool):
    """格式化并整理本地笔记。"""
    
    @property
    def name(self) -> str:
        return "organize_notes"

    async def description(self) -> str:
        return "格式化并整理本地 Markdown 笔记。自动补充 YAML 标签、统一层级标题、加粗关键词并安全覆写原文件。"

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, args: Optional[dict] = None) -> bool:
        # 修改文件属于写操作，必须抛出权限请求让用户审核
        return True

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "格式化并整理指定目录下的 Markdown 笔记，利用 LLM 进行重新排版和优化。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "目标文件夹的绝对路径。如果为空，将自动从 routing_rules.md 中读取默认的【学习笔记根目录】",
                        },
                        "days_ago": {
                            "type": "integer",
                            "description": "扫描多少天内修改过的笔记进行整理。默认值为 3 天。",
                            "default": 3
                        }
                    },
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        dir_path = input_args.get("directory", "")
        if dir_path and not Path(dir_path).exists():
            return {"result": False, "message": f"目录不存在: {dir_path}"}
        return {"result": True, "message": ""}

    async def call(self, args: dict, context=None) -> AsyncGenerator[ToolResult, None]:
        target_dir = args.get("directory", "")
        if not target_dir:
            import re
            rules = context.memory.get_routing_rules() if context and hasattr(context, "memory") else ""
            m = re.search(r'学习笔记根目录:\s*(.+)', rules)
            if m:
                target_dir = m.group(1).strip()
            else:
                target_dir = "/Users/xiaofeng/Documents/学习笔记"
            
        days_ago = args.get("days_ago", 3)
        base_path = Path(target_dir)
        
        if not base_path.exists() or not base_path.is_dir():
            yield ToolResult(type="result", data=f"Error: 目录不存在或不是文件夹: {target_dir}")
            return
            
        yield ToolResult(type="progress", data=f"开始扫描 {target_dir} 下近 {days_ago} 天修改的 Markdown 文件...")
        
        cutoff_time = datetime.now() - timedelta(days=days_ago)
        cutoff_timestamp = cutoff_time.timestamp()
        
        target_files = []
        for root, dirs, files in os.walk(base_path):
            # 过滤掉隐藏目录和系统文件
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if file.endswith('.md'):
                    full_path = Path(root) / file
                    try:
                        if full_path.stat().st_mtime >= cutoff_timestamp:
                            target_files.append(full_path)
                    except Exception:
                        pass
                        
        if not target_files:
            yield ToolResult(type="result", data=f"未找到近 {days_ago} 天内修改过的 .md 文件，无需整理。")
            return
            
        yield ToolResult(type="progress", data=f"共找到 {len(target_files)} 个待整理文件。")
        
        if not context or not context.llm:
            yield ToolResult(type="result", data="Error: 缺少 LLM 客户端上下文，无法进行格式化。")
            return
            
        processed_count = 0
        failed_count = 0
        details = []
        
        prompt_template = """你是一个专业的 Markdown 笔记排版专家。请严格按照以下规范对这篇笔记进行重写和排版，绝不能删减、改变原有正文的核心意思，也不要随意删减代码块：
1. 添加或更新 YAML Frontmatter (必须包含 title, date, tags)。如果已有则完善它。
2. 规范化层级标题 (#, ##, ###)，确保结构清晰。
3. 加粗关键专业术语，提升阅读体验。
4. 修复乱码或排版混乱的地方。

以下是需要整理的笔记原文：
---
{content}
---

请只输出重写后的 Markdown 全文，不要输出你的思考过程，不要输出前后寒暄，也不要在最外层包裹 ```markdown 和 ``` 标记符。"""
        
        for file_path in target_files:
            yield ToolResult(type="progress", data=f"正在整理: {file_path.name}")
            try:
                content = file_path.read_text(encoding='utf-8', errors='replace')
                # 防呆：检查内容大小，避免撑爆 Token
                if len(content) > 15000:
                    details.append(f"跳过 {file_path.name}: 文件过大 (>15000 字符)")
                    failed_count += 1
                    continue
                    
                prompt = prompt_template.format(content=content)
                res = await context.llm.chat(messages=[{"role": "user", "content": prompt}])
                new_content = res.get("content", "").strip()
                
                if not new_content:
                    details.append(f"失败 {file_path.name}: LLM 返回为空")
                    failed_count += 1
                    continue
                    
                # 防呆：强制剥离 LLM 可能胡乱添加的 ```markdown 外壳
                if new_content.startswith("```markdown"):
                    new_content = new_content[len("```markdown"):].lstrip()
                elif new_content.startswith("```md"):
                    new_content = new_content[len("```md"):].lstrip()
                elif new_content.startswith("```"):
                    new_content = new_content[len("```"):].lstrip()
                    
                if new_content.endswith("```"):
                    new_content = new_content[:-3].rstrip()
                    
                # 防呆：物理备份 (.bak)
                backup_path = file_path.with_suffix('.md.bak')
                shutil.copy2(file_path, backup_path)
                
                # 安全覆写
                file_path.write_text(new_content, encoding='utf-8')
                details.append(f"成功 {file_path.name} (已备份至 {backup_path.name})")
                processed_count += 1
                
            except Exception as e:
                details.append(f"错误 {file_path.name}: {str(e)}")
                failed_count += 1
                
        summary = f"笔记整理完毕。成功: {processed_count} 个，失败/跳过: {failed_count} 个。\n详细信息:\n" + "\n".join(details)
        yield ToolResult(type="result", data=summary)
