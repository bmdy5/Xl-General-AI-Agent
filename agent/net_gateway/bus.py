import time
import asyncio
import logging

logger = logging.getLogger("net_gateway.bus")

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


class TokenBucketLimiter:
    """全局物理发包平滑流控令牌桶限流器。
    
    采用高并发无锁死 (Lock-free/Timer-based) 排队算法，
    并发调用时仅在微秒级入锁更新未来的绝对时间指针，
    并在锁的外部执行 asyncio.sleep() 等待，彻底解决 sleep 霸占锁导致的高并发串行死锁。
    """
    
    def __init__(self, capacity: float = 5.0, refill_rate: float = 0.67):
        self.capacity = capacity
        self.refill_rate = refill_rate
        # 1.0 / refill_rate 表示产生 1 个令牌所需的物理时间 (秒)
        self.interval = 1.0 / refill_rate
        self.max_tokens = capacity
        
        # 记录下一次允许无延时发送的单调时间戳
        self.allow_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """获取发包令牌。采用微秒级短锁计算发包排队时间，并在锁外并发挂起。"""
        wait_time = 0.0
        async with self._lock:
            now = time.monotonic()
            
            # 如果 allow_at 在过去太远，说明长期未发包，令牌爆满，重置 allow_at
            # 最多允许积攒容量为 max_tokens 的爆发力，即最多回退 (max_tokens * interval)
            max_backlog = self.max_tokens * self.interval
            if now - self.allow_at > max_backlog:
                self.allow_at = now - max_backlog
                
            # 判定当前时间是否已经到了允许发包的时间
            if now >= self.allow_at:
                # 扣除 1 个令牌的等价时间
                self.allow_at = self.allow_at + self.interval
                # 如果 allow_at 仍小于 now，重置为 now + interval
                if self.allow_at < now:
                    self.allow_at = now + self.interval
                wait_time = 0.0
            else:
                # 令牌不足，为当前协程预支分配下一次允许的时间
                wait_time = self.allow_at - now
                self.allow_at = self.allow_at + self.interval
                
        # 锁外挂起：锁已被微秒级释放，其他协程可以瞬间入锁参与令牌时间排队
        if wait_time > 0.0:
            await asyncio.sleep(wait_time)

