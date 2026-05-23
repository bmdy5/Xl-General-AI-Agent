import time
import asyncio
import logging

logger = logging.getLogger("net_gateway.carrier")

class CSMAController:
    """CSMA/CD 载波冲突检测总线控制器，处理高并发防抢话和打盹退避策略"""
    
    def __init__(self, context, backoff_seconds=1.2):
        self.context = context
        self.backoff_seconds = backoff_seconds

    def register_message(self, session_key: str) -> float:
        """注册新进入的消息，记录单调发言时间戳，用来进行载波监听抢占判定"""
        this_msg_time = time.monotonic()
        self.context._last_receive_time[session_key] = this_msg_time
        return this_msg_time

    async def wait_for_carrier_sense(self, session_key: str, this_msg_time: float) -> bool:
        """载波退避等待。如果在退避等待期间有最新发言，返回 True 表示冲突发生应抢占终止"""
        await asyncio.sleep(self.backoff_seconds)
        
        latest_time = self.context._last_receive_time.get(session_key, 0.0)
        if latest_time > this_msg_time:
            logger.info(f"Carrier Sense [Collision Detected]: Newer message received for {session_key}. Quietly aborting current handler.")
            return True
        return False

    def is_collision(self, session_key: str, task_start_time: float) -> bool:
        """检测在大模型推理期间对方是否有新发言，若有则触发 CD 碰撞中断，废弃当前输出"""
        latest_time = self.context._last_receive_time.get(session_key, 0.0)
        if latest_time > task_start_time:
            logger.info(f"Collision Detection [Active打断]: Detected newer user speech during LLM reasoning for {session_key}. Aborting handler.")
            return True
        return False
