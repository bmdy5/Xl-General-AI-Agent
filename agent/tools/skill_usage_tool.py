import logging
from typing import AsyncGenerator
from agent.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("agent.tools.skill_usage")

class RecordSkillUsageTool(BaseTool):
    name = "record_skill_usage"
    description = "记录技能/经验使用情况，帮助系统筛选优秀经验并晋级为技能。"

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
                        "skill_name": {"type": "string", "description": "技能/经验名称"},
                        "success": {"type": "boolean", "description": "是否成功"}
                    },
                    "required": ["skill_name", "success"]
                }
            }
        }

    async def call(self, args: dict, context) -> AsyncGenerator[ToolResult, None]:
        import sqlite3
        from datetime import datetime, timezone
        from agent.memory.manager import MemoryManager
        from agent.core.config import settings

        skill_name = args.get("skill_name")
        success = args.get("success", True)

        try:
            mm = MemoryManager()
            db = mm._get_db()
            db_path = mm.base_dir / "memories.db"
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # 1. SQLite skill_usage 统计表
            conn = sqlite3.connect(str(db_path), timeout=10.0)
            try:
                with conn:
                    conn.execute("""CREATE TABLE IF NOT EXISTS skill_usage (
                        skill_name TEXT PRIMARY KEY, usage_count INTEGER DEFAULT 0,
                        success_count INTEGER DEFAULT 0, is_skills INTEGER DEFAULT 0, last_used TEXT)""")
                    conn.execute("""INSERT INTO skill_usage (skill_name, usage_count, success_count, is_skills, last_used)
                        VALUES (?, 1, ?, 0, ?) ON CONFLICT(skill_name) DO UPDATE SET
                        usage_count = usage_count + 1, success_count = success_count + ?, last_used = ?""",
                        (skill_name, 1 if success else 0, now_str, 1 if success else 0, now_str))
                    cur = conn.execute("SELECT usage_count, success_count, is_skills FROM skill_usage WHERE skill_name = ?", (skill_name,))
                    row = cur.fetchone()
                    db_usage, db_success, is_skills = row if row else (1, 1 if success else 0, 0)
            finally:
                conn.close()

            # 2. 查找 knowledge_items 中的经验/技能记录
            cur = db.execute(
                "SELECT id, ki_type, visit_count FROM knowledge_items WHERE title LIKE ? AND ki_type IN ('experience','skill') LIMIT 1",
                (f"%{skill_name}%",))
            ki_row = cur.fetchone()

            if ki_row:
                ki_id, ki_type = ki_row[0], ki_row[1]
                # 更新 visit_count
                db.execute("UPDATE knowledge_items SET visit_count = visit_count + 1, last_hit_at = ? WHERE id = ?",
                           (now_str, ki_id))
                db.commit()
            else:
                # fallback: 读旧 .md 文件
                from agent.core.paths import EXPERIENCES_DIR
                skill_file = EXPERIENCES_DIR / f"{skill_name}.md"
                if skill_file.exists():
                    ki_type = "experience"
                else:
                    ki_type = "experience"  # 默认

            # 3. 晋升逻辑：experience → skill
            is_promoted = False
            success_rate = db_success / db_usage if db_usage > 0 else 0.0
            promo_usage = settings.get_threshold("skill_promotion_usage", 5)
            promo_rate = settings.get_threshold("skill_promotion_success_rate", 0.90)

            if ki_row and ki_type == "experience" and not is_skills and db_usage >= promo_usage and success_rate >= promo_rate:
                db.execute("UPDATE knowledge_items SET ki_type = 'skill', updated_at = ? WHERE id = ?", (now_str, ki_id))
                db.commit()
                conn = sqlite3.connect(str(db_path), timeout=10.0)
                try:
                    with conn:
                        conn.execute("UPDATE skill_usage SET is_skills = 1 WHERE skill_name = ?", (skill_name,))
                finally:
                    conn.close()
                is_promoted = True

            logger.info(f"Recorded skill usage for {skill_name} (usage={db_usage}, rate={success_rate:.2f})")

            if is_promoted:
                msg = f"技能晋级: {skill_name} (使用{db_usage}次, 成功率{success_rate*100:.0f}%)"
            else:
                msg = f"已记录: {skill_name} (使用{db_usage}次, 成功率{success_rate*100:.0f}%)"

            yield ToolResult(type="result", data=msg, result_for_assistant=msg)

        except Exception as e:
            logger.error(f"Failed to record skill usage: {e}")
            yield ToolResult(type="result", data=f"Error: {e}", result_for_assistant=f"Error: {e}")
