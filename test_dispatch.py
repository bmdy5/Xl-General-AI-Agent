import asyncio
import os
from agent.core.bootstrap import setup_system, build_agent
from agent.core.gateway import QQGateway

async def main():
    setup_system()
    bot = QQGateway(build_agent)
    admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
    event = {
        "post_type": "message",
        "message_type": "private",
        "user_id": admin_id,
        "sender": {"nickname": "亮哥"},
        "raw_message": "小萤，测试一下日志系统的重构效果，请调用 read_file 工具读取一下 openspec/changes/refactor-logging/tasks.md，然后告诉我日志是不是分流了。"
    }
    await bot.dispatcher.dispatch_event(event)
    
    # 等待异步任务实际执行完成
    session_key = f"user_{admin_id}"
    for _ in range(50):  # 等待任务启动并获取
        active_task = bot.get_active_task(session_key)
        if active_task and not isinstance(active_task, bool):
            print(f"检测到正在运行的后台 Agent 任务，开始等待其完成...")
            await active_task
            break
        await asyncio.sleep(0.1)
    else:
        print("未检测到活跃的后台 Agent 任务。")

if __name__ == "__main__":
    asyncio.run(main())
