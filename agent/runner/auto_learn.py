import os
from ..core.bootstrap import build_agent
from ..learn.auto_learn import AutoLearner

async def run_auto_learn():
    """自主学习模式：agent 自动浏览网页、学习知识、创建技能."""
    agent = build_agent()
    learn_model = os.environ.get("MYAGENT_LEARN_MODEL", "")
    dash = getattr(agent, '_dash', None)
    learner = AutoLearner(agent, max_duration_minutes=5, learn_model=learn_model, dashboard=dash)

    print("\n  MyAgent — 自主学习模式")
    print(f"  Model: {agent.llm.model}")
    print(f"  Duration: 5 minutes")
    print(f"  Knowledge base: {learner.kb}")
    print()

    try:
        result = await learner.run()
    except KeyboardInterrupt:
        result = {
            "articles_read": 0, 
            "skills_created": 0, 
            "topics": [], 
            "summary": "用户中断", 
            "errors": ["KeyboardInterrupt"]
        }

    print(f"\n  ===== 学习完成 =====")
    print(f"  阅读文章: {result['articles_read']} 篇")
    print(f"  创建技能: {result['skills_created']} 个")
    if result["errors"]:
        print(f"  错误: {len(result['errors'])} 个")
    print(f"\n{result['summary']}")
