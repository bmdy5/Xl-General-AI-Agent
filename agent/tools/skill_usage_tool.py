import re
import logging
from typing import AsyncGenerator
from pathlib import Path
from agent.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("agent.tools.skill_usage")

class RecordSkillUsageTool(BaseTool):
    name = "record_skill_usage"
    description = "主动申报并记录某项技能/经验的使用情况。用于增加目标经验文件的 usage_count 和 success_count。这会帮助系统筛选出最优秀的实战经验并淘汰无用经验。"

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: dict = None) -> bool:
        return False

    async def validate_input(self, input_args: dict, context=None) -> dict:
        if "skill_name" not in input_args:
            return {"result": False, "message": "skill_name is required"}
        return {"result": True, "message": "ok"}

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
        import sqlite3
        import shutil
        from datetime import datetime, timezone
        from agent.memory.manager import MemoryManager

        skill_name = args.get("skill_name")
        success = args.get("success", True)
        
        try:
            exp_dir = Path(__file__).resolve().parents[2] / "agent_memory" / "experiences"
            skills_dir = Path(__file__).resolve().parents[2] / "agent_memory" / "skills"
            skill_file = exp_dir / f"{skill_name}.md"
            skills_file = skills_dir / f"{skill_name}.md"
            
            target_file = None
            if skill_file.exists():
                target_file = skill_file
            elif skills_file.exists():
                target_file = skills_file
                
            if not target_file:
                yield ToolResult(type="result", data=f"Error: 找不到名为 {skill_name}.md 的技能或经验文件。", result_for_assistant=f"Error: 找不到名为 {skill_name}.md 的技能或经验文件。")
                return
                
            content = target_file.read_text(encoding="utf-8")
            
            # 1. 物理更新 Markdown 文本中的 frontmatter 计数
            usage_match = re.search(r'^usage_count:\s*(\d+)$', content, flags=re.MULTILINE)
            success_match = re.search(r'^success_count:\s*(\d+)$', content, flags=re.MULTILINE)
            
            new_usage = 1
            new_success = 1 if success else 0
            
            if usage_match:
                new_usage = int(usage_match.group(1)) + 1
                content = re.sub(r'^usage_count:\s*\d+$', f"usage_count: {new_usage}", content, count=1, flags=re.MULTILINE)
                
            if success_match:
                new_success = int(success_match.group(1)) + (1 if success else 0)
                content = re.sub(r'^success_count:\s*\d+$', f"success_count: {new_success}", content, count=1, flags=re.MULTILINE)
                
            target_file.write_text(content, encoding="utf-8")
            
            # 2. 写入并累加 SQLite 数据库的统计打分
            mm = MemoryManager()
            db_path = mm.base_dir / "memories.db"
            
            db_usage, db_success, is_skills = new_usage, new_success, 0
            
            conn = sqlite3.connect(str(db_path), timeout=10.0)
            try:
                with conn:
                    # 自动建表自愈
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS skill_usage (
                            skill_name TEXT PRIMARY KEY,
                            usage_count INTEGER DEFAULT 0,
                            success_count INTEGER DEFAULT 0,
                            is_skills INTEGER DEFAULT 0,
                            last_used TEXT
                        )
                    """)
                    
                    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    # 执行 upsert 统计
                    conn.execute("""
                        INSERT INTO skill_usage (skill_name, usage_count, success_count, is_skills, last_used)
                        VALUES (?, ?, ?, 0, ?)
                        ON CONFLICT(skill_name) DO UPDATE SET
                            usage_count = usage_count + 1,
                            success_count = success_count + ?,
                            last_used = ?
                    """, (skill_name, 1, 1 if success else 0, now_str, 1 if success else 0, now_str))
                    
                    # 提取最新 DB 统计以确保与 md 一致
                    cur = conn.execute("SELECT usage_count, success_count, is_skills FROM skill_usage WHERE skill_name = ?", (skill_name,))
                    row = cur.fetchone()
                    if row:
                        db_usage, db_success, is_skills = row
            except Exception as db_err:
                logger.error(f"Failed to update sqlite skill_usage: {db_err}")
            finally:
                conn.close()
                
            # 3. 动态评分晋升校验 (使用次数 >= 5, 成功率 >= 90%, 且当前依然是 experiences 经验)
            success_rate = db_success / db_usage if db_usage > 0 else 0.0
            is_promoted = False
            
            if target_file == skill_file and not is_skills and db_usage >= 5 and success_rate >= 0.90:
                skills_dir.mkdir(parents=True, exist_ok=True)
                dest_file = skills_dir / f"{skill_name}.md"
                
                # 安全移动（重名自愈覆盖）
                if dest_file.exists():
                    dest_file.unlink()
                shutil.move(str(skill_file), str(dest_file))
                
                # 同步更新 DB 中已晋升状态
                conn = sqlite3.connect(str(db_path), timeout=10.0)
                try:
                    with conn:
                        conn.execute("UPDATE skill_usage SET is_skills = 1 WHERE skill_name = ?", (skill_name,))
                except Exception as up_err:
                    logger.error(f"Failed to update SQLite is_skills to 1: {up_err}")
                finally:
                    conn.close()
                    
                is_promoted = True
                
            logger.info(f"Recorded skill usage for {skill_name} (success={success}, usage={db_usage}, success_rate={success_rate:.2f})")
            
            if is_promoted:
                msg = f"🎉 [技能进化成功] 恭喜！实战避坑经验 【{skill_name}】 实战打分优秀（使用次数 {db_usage} 次，成功率 {success_rate*100:.1f}%），已正式无缝晋升为小萤常驻肌肉技能！"
            else:
                msg = f"Successfully recorded usage for {skill_name} (Total Usage: {db_usage}, Success Rate: {success_rate*100:.1f}%). Keep it up!"
                
            yield ToolResult(type="result", data=msg, result_for_assistant=msg)
            
        except Exception as e:
            logger.error(f"Failed to record skill usage: {e}")
            yield ToolResult(type="result", data=f"Error recording skill usage: {e}", result_for_assistant=f"Error: {e}")
