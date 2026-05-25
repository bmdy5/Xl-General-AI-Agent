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

    def log_metrics(self, session_key: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int, total_tokens: int, is_estimated: bool = False, user_id: str = None):
        """统一、高画质结构化记录本轮对话的 Token 与 caching 缓存命中审计日志"""
        hit_rate = (cached_tokens / prompt_tokens * 100) if prompt_tokens > 0 else 0.0
        status_desc = " (智能估算)" if is_estimated else ""
        
        log_content = (
            f"本次推理完成。大模型总共消耗: {total_tokens} Tokens{status_desc} | "
            f"Prompt: {prompt_tokens} (Cached: {cached_tokens}, Hit Rate: {hit_rate:.1f}%) | "
            f"Completion: {completion_tokens}"
        )
        # 1. 写入物理分流轨迹日志 (agent_activity.log / coworker_activity.log)
        self.log_activity("系统调度", log_content, user_id=user_id)
        
        # 2. 同时向控制台标准日志中广播，使其自动呈现在 gateway.log 中，做到所有日志 100% 全指标能力大一统！
        logger.info(f"📊 [UNIFIED TOKEN AUDIT] Session: {session_key} | {log_content}")
