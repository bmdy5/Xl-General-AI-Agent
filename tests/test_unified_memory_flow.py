import os
import re
import json
import sqlite3
import shutil
import pytest
from pathlib import Path
from datetime import datetime, timezone

from agent.memory.manager import MemoryManager
from agent.evolution.memory import extract_coworker_memory
from agent.tools.skill_usage_tool import RecordSkillUsageTool
from agent.core.prompt_builder import _search_experiences

@pytest.fixture(autouse=True)
def setup_test_sandbox():
    """为每次测试提供干净独立的沙箱隔离记忆脑区环境"""
    project_root = Path(__file__).resolve().parents[1]
    test_isolated_dir = project_root / "agent_memory" / "core" / "test_isolated"
    
    # 强制安全清理之前的脏数据
    if test_isolated_dir.exists():
        shutil.rmtree(test_isolated_dir)
    test_isolated_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建临时的测试 sub-experiences 与 sub-skills 目录
    test_exp_dir = project_root / "agent_memory" / "experiences"
    test_skills_dir = project_root / "agent_memory" / "skills"
    test_context_dir = project_root / "agent_memory" / "context"
    
    for d in [test_exp_dir, test_skills_dir, test_context_dir]:
        d.mkdir(parents=True, exist_ok=True)
        
    # 清空 SQLite 中可能残留的 test_case_ 前缀脏测试记录
    try:
        mm = MemoryManager()
        db_path = mm.base_dir / "memories.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            with conn:
                conn.execute("DELETE FROM skill_usage WHERE skill_name LIKE 'test_case_%'")
            conn.close()
    except Exception:
        pass

    yield {
        "isolated_dir": test_isolated_dir,
        "exp_dir": test_exp_dir,
        "skills_dir": test_skills_dir,
        "context_dir": test_context_dir
    }
    
    # 测试完毕后清理临时创建的测试用例文件，防止污染生产脑区
    for f in test_exp_dir.glob("test_case_*.md"):
        f.unlink()
    for f in test_skills_dir.glob("test_case_*.md"):
        f.unlink()
    for f in test_context_dir.glob("coworker_test_*.json"):
        f.unlink()

@pytest.mark.asyncio
async def test_coworker_context_memory_flow(setup_test_sandbox):
    """测试 Task 2.3 & 4.2: 社交经历 coworker 极简隔离记忆 json 在 agent_memory/context 中的正确存取"""
    class MockLLM:
        model = "mock-model"
        async def chat(self, messages, tools=None, model_override=None):
            return {"content": '{"memories": ["偏好使用 Python", "不喜废话"]}'}
            
    class MockAgent:
        current_user_id = "test_user_123"
        messages = [
            {"role": "user", "content": "你好小萤"},
            {"role": "assistant", "content": "你好！有什么我可以帮你的？"},
            {"role": "user", "content": "我写代码喜欢用 Python，比较追求极简"},
            {"role": "assistant", "content": "好哒，我记住啦！"}
        ]
        llm = MockLLM()
        
    # 模拟提取记忆
    await extract_coworker_memory(MockAgent())
    
    # 核验写入物理路径
    target_json = setup_test_sandbox["context_dir"] / "coworker_test_user_123.json"
    assert target_json.exists(), f"社交记忆 json 未成功写入目标路径: {target_json}"
    
    data = json.loads(target_json.read_text(encoding="utf-8"))
    assert "memories" in data
    assert "偏好使用 Python" in data["memories"]
    assert len(data["memories"]) <= 3

def test_experience_rag_search_flow(setup_test_sandbox):
    """测试 Task 2.2 & 4.3: 避坑经验在 agent_memory/experiences/ 中的 RAG 检索命中挂载"""
    exp_dir = setup_test_sandbox["exp_dir"]
    
    # 写入一条模拟避坑经验
    test_exp_file = exp_dir / "test_case_avoid_git_conflict.md"
    content = """---
name: test_case_avoid_git_conflict
trigger: git/冲突/分支/merge
description: 模拟如何安全解决 Git 冲突避坑指南
usage_count: 0
success_count: 0
category: verification
---

# 模拟避坑指南
在合并分支前，必须先执行 git fetch origin 保证绝对安全！
"""
    test_exp_file.write_text(content, encoding="utf-8")
    
    # 运行 RAG 检索匹配
    matched_block = _search_experiences("我今天写代码遇到了 git 冲突怎么办")
    
    assert "[DYNAMIC EXPERIENCE BLOCK]" in matched_block
    assert "git fetch origin" in matched_block
    assert "test_case_avoid_git_conflict" in matched_block

@pytest.mark.asyncio
async def test_experience_to_skills_promotion_flow(setup_test_sandbox):
    """测试 Task 3.1 & 3.2 & 4.4: 累加打分、SQLite 同步以及满 5 次 90% 成功率时 Experiences ➔ Skills 的物理搬家晋升"""
    exp_dir = setup_test_sandbox["exp_dir"]
    skills_dir = setup_test_sandbox["skills_dir"]
    
    # 建立一条临时测试经验
    skill_name = "test_case_hotkey_run"
    exp_file = exp_dir / f"{skill_name}.md"
    content = f"""---
name: {skill_name}
trigger: 快捷键/运行/调试
description: 模拟快捷键高效调试的经历
usage_count: 0
success_count: 0
category: verification
---

# 模拟调试
1. 按 Command+R 启动极速调试。
"""
    exp_file.write_text(content, encoding="utf-8")
    
    tool = RecordSkillUsageTool()
    
    # 1. 模拟 4 次成功打卡，此时 usage_count=4，应该依然保留在 experiences 目录下
    for _ in range(4):
        res_list = []
        async for res in tool.call({"skill_name": skill_name, "success": True}, context=None):
            res_list.append(res)
        assert "Successfully recorded" in res_list[0].data
        
    assert exp_file.exists()
    assert not (skills_dir / f"{skill_name}.md").exists()
    
    # 2. 模拟第 5 次打卡（success），达到 5 次且成功率 100% (>=90%) 门槛，触发自动物理搬家！
    res_list = []
    async for res in tool.call({"skill_name": skill_name, "success": True}, context=None):
        res_list.append(res)
        
    assert "技能进化成功" in res_list[0].data
    
    # 3. 验证物理搬家流转结果：experiences/ 文件消失，完美晋升并出现在 skills/ 目录下！
    assert not exp_file.exists(), "原 experiences 中的经验文件没有被清理剪切！"
    promoted_file = skills_dir / f"{skill_name}.md"
    assert promoted_file.exists(), "晋升后的常驻技能文件没有在 skills/ 目录正确生成！"
    
    # 4. 验证 SQLite 中的 is_skills 标记为 1
    mm = MemoryManager()
    db_path = mm.base_dir / "memories.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT usage_count, success_count, is_skills FROM skill_usage WHERE skill_name = ?", (skill_name,))
        row = cur.fetchone()
        assert row is not None
        db_usage, db_success, is_skills = row
        assert db_usage == 5
        assert db_success == 5
        assert is_skills == 1
    finally:
        conn.close()
