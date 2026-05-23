import os
import re
from datetime import datetime
import logging

logger = logging.getLogger("net_gateway.logger")

class ActivityLogger:
    """网关活动轨迹与沙箱旁路分流文件日志记录器"""
    
    def __init__(self, bot):
        self.bot = bot
        from pathlib import Path
        root_dir = Path(__file__).resolve().parents[2]
        self._activity_log_path = str(root_dir / "logs" / "agent_activity.log")
        self._bypass_log_path = str(root_dir / "logs" / "coworker_activity.log")

    def log_activity(self, category: str, content: str, user_id: str = None):
        """结构化轨迹活动日志记录，支持根据发言人身份将主流量与沙箱旁路流量物理隔离分流"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_content = content
        for pattern in [r"(?i)token[s]?'?\s*:\s*'[^']+'", r"(?i)key[s]?'?\s*:\s*'[^']+'"]:
            safe_content = re.sub(pattern, "token: '******'", safe_content)
        
        if len(safe_content) > 1000:
            safe_content = safe_content[:1000] + " ... (truncated)"
        
        log_line = f"{now} | [{category}] | {safe_content}\n"
        
        # 物理路由判定：亮哥本人的轨迹打入主要日志，其他人的动作全数隔离归入旁路日志
        target_path = self._activity_log_path
        if user_id is not None:
            if str(user_id) != str(self.bot.admin_id):
                target_path = self._bypass_log_path
                
        try:
            with open(target_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.error(f"Failed to write activity log: {e}")
