import re
import logging
from typing import AsyncGenerator
from pathlib import Path
from agent.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("agent.tools.skill_usage")

class RecordSkillUsageTool(BaseTool):
    name = "record_skill_usage"
    description = "主动申报并记录某项技能/经验的使用情况。用于增加目标经验文件的 usage_count 和 success_count。这会帮助系统筛选出最优秀的实战经验并淘汰无用经验。"

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "使用的技能或经验的文件名（不含 .md 后缀）"
                        },
                        "success": {
                            "type": "boolean",
                            "description": "技能执行是否成功解决问题"
                        }
                    },
                    "required": ["skill_name", "success"]
                }
            }
        }

    async def call(self, args: dict, context) -> AsyncGenerator[ToolResult, None]:
        skill_name = args.get("skill_name")
        success = args.get("success", True)
        
        try:
            exp_dir = Path(__file__).resolve().parents[2] / "experience"
            skill_file = exp_dir / f"{skill_name}.md"
            
            if not skill_file.exists():
                yield ToolResult(type="result", data=f"Error: 找不到名为 {skill_name}.md 的技能或经验文件。", result_for_assistant=f"Error: 找不到名为 {skill_name}.md 的技能或经验文件。")
                return
                
            content = skill_file.read_text(encoding="utf-8")
            
            # 简单粗暴的正则替换来增加计数 (寻找 usage_count: N)
            usage_match = re.search(r'^usage_count:\s*(\d+)$', content, flags=re.MULTILINE)
            success_match = re.search(r'^success_count:\s*(\d+)$', content, flags=re.MULTILINE)
            
            if usage_match:
                count = int(usage_match.group(1)) + 1
                content = re.sub(r'^usage_count:\s*\d+$', f"usage_count: {count}", content, count=1, flags=re.MULTILINE)
                
            if success and success_match:
                count = int(success_match.group(1)) + 1
                content = re.sub(r'^success_count:\s*\d+$', f"success_count: {count}", content, count=1, flags=re.MULTILINE)
                
            skill_file.write_text(content, encoding="utf-8")
            logger.info(f"Recorded skill usage for {skill_name} (success={success})")
            
            msg = f"Successfully recorded usage for {skill_name}. Keep it up!"
            yield ToolResult(type="result", data=msg, result_for_assistant=msg)
            
        except Exception as e:
            logger.error(f"Failed to record skill usage: {e}")
            yield ToolResult(type="result", data=f"Error recording skill usage: {e}", result_for_assistant=f"Error: {e}")
