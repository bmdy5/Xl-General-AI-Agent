import os
import re
from datetime import datetime
import logging

logger = logging.getLogger("net_gateway.logger")

class ActivityLogger:
    """网关活动轨迹与沙箱旁路分流日志广播器"""
    
    def __init__(self, bot):
        self.bot = bot
        self._activity_logger = logging.getLogger("agent.activity.gateway")

    def log_activity(self, category: str, content: str, user_id: str = None):
        """结构化轨迹活动日志记录，支持将主流量与沙箱旁路流量进行广播"""
        safe_content = content
        for pattern in [r"(?i)token[s]?'?\s*:\s*'[^']+'", r"(?i)key[s]?'?\s*:\s*'[^']+'"]:
            safe_content = re.sub(pattern, "token: '******'", safe_content)
        
        if len(safe_content) > 1000:
            safe_content = safe_content[:1000] + " ... (truncated)"
        
        # 通过 logging 总线直接广播，利用 ContextFormatter 统一加盖时间戳和 Session ID，由 bootstrap.py 的轮转 Handler 写入 activity.log
        self._activity_logger.info(f"[{category}] | {safe_content}")

    def log_metrics(self, session_key: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int, total_tokens: int, is_estimated: bool = False, user_id: str = None):
        """统一、高画质结构化记录本轮对话的 Token 与 caching 缓存命中审计日志"""
        hit_rate = (cached_tokens / prompt_tokens * 100) if prompt_tokens > 0 else 0.0
        status_desc = " (智能估算)" if is_estimated else ""
        
        log_content = (
            f"本次推理完成。大模型总共消耗: {total_tokens} Tokens{status_desc} | "
            f"Prompt: {prompt_tokens} (Cached: {cached_tokens}, Hit Rate: {hit_rate:.1f}%) | "
            f"Completion: {completion_tokens}"
        )
        # 1. 写入物理分流轨迹日志 (activity.log)
        self.log_activity("系统调度", log_content, user_id=user_id)
        
        # 2. 同时向控制台标准日志中广播，使其自动呈现在 gateway.log 中，做到所有日志 100% 全指标能力大一统！
        logger.info(f"📊 [UNIFIED TOKEN AUDIT] Session: {session_key} | {log_content}")
