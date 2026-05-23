class EventMiddleware:
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        """
        处理事件。
        返回 True 表示事件已被拦截/处理完成，管道应该停止后续执行。
        返回 False 表示事件未被拦截，管道继续向下传递。
        """
        raise NotImplementedError
