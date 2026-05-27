import os
import shutil
import pytest
import threading
import time
from pathlib import Path
from agent.core.prompt_builder import (
    _strip_yaml_frontmatter,
    _parse_yaml_frontmatter,
    _calculate_skill_score,
    _load_core_skills,
    rules_lock
)

@pytest.fixture(scope="module")
def mock_skills_setup():
    """单元测试夹具：动态创建临时测试技能物理目录与资产，测试后自愈清理"""
    skills_dir = Path(__file__).resolve().parents[1] / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. 物理测试技能 A (扁平 Markdown，中度相关技能)
    skill_a_path = skills_dir / "test_temp_flat_skill.md"
    skill_a_content = (
        "---\n"
        "name: 临时平铺测试技能\n"
        "trigger: 数据库, 物理存储, 检索\n"
        "version: 1.2\n"
        "usage_count: 5\n"
        "---\n\n"
        "# 临时平铺测试技能\n"
        "SOP 步骤：\n"
        "1. 执行数据库查询。\n"
        "2. 清理物理缓存。\n"
    )
    skill_a_path.write_text(skill_a_content, encoding="utf-8")
    
    # 2. 物理测试技能 B (子目录伞状技能，高度相关技能)
    skill_b_dir = skills_dir / "test_temp_umbrella_dir"
    skill_b_dir.mkdir(parents=True, exist_ok=True)
    skill_b_md = skill_b_dir / "SKILL.md"
    skill_b_content = (
        "---\n"
        "name: 临时伞状测试技能\n"
        "trigger: 清理日志/重构/任务/开发\n"
        "version: 2.1\n"
        "---\n\n"
        "# 临时伞状测试技能\n"
        "SOP 步骤：\n"
        "1. 停止网关。\n"
        "2. 归档日志。\n"
    )
    skill_b_md.write_text(skill_b_content, encoding="utf-8")
    
    # 3. 注入辅助资产 (templates, scripts)
    templates_dir = skill_b_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    (templates_dir / "mock_config.yaml").write_text("config: test", encoding="utf-8")
    
    scripts_dir = skill_b_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "mock_verify.sh").write_text("echo 'success'", encoding="utf-8")
    
    yield skills_dir
    
    # 清理所有临时创建的测试物理资产，保持环境洁净
    try:
        if skill_a_path.exists():
            skill_a_path.unlink()
        if skill_b_dir.exists():
            shutil.rmtree(skill_b_dir)
    except Exception:
        pass

def test_strip_yaml_frontmatter():
    """测试 YAML 表头物理脱水剥离功能"""
    content = "---\ntrigger: test\nversion: 1.0\n---\n\n# Body Content"
    stripped = _strip_yaml_frontmatter(content).strip()
    assert stripped == "# Body Content"

def test_strip_yaml_frontmatter_greedy_defense():
    """方案 A 补强测试：正文含 --- 水平线时，严格非贪婪匹配防御，不误伤吞噬正文"""
    content = (
        "---\n"
        "name: test_greedy\n"
        "trigger: test\n"
        "---\n\n"
        "# 核心标题\n"
        "--- (正文水平分割线)\n"
        "底部补充细节"
    )
    stripped = _strip_yaml_frontmatter(content).strip()
    assert "# 核心标题" in stripped
    assert "底部补充细节" in stripped
    assert "--- (正文水平分割线)" in stripped
    # 确保没有发生大跨度吞噬，BOM 表头正确剥离
    assert "name: test_greedy" not in stripped

def test_parse_yaml_frontmatter():
    """测试 YAML 头部安全且高容错解析"""
    content = "---\nname: 调试工具\ntrigger: 调试/日志/错误\nversion: 3.5\n---\n\n# Body"
    meta = _parse_yaml_frontmatter(content)
    assert meta.get("name") == "调试工具"
    assert meta.get("trigger") == "调试/日志/错误"
    assert meta.get("version") == 3.5 or meta.get("version") == "3.5"

def test_calculate_skill_score():
    """方案 A 补强测试：精确算分机制 (词长权重、完全匹配加成)"""
    trigger_str = "清理日志/重构/任务/开发"
    file_name = "test_temp.md"
    
    # 1. 整体子串完全匹配奖励 => 10.0 分
    assert _calculate_skill_score("清理日志/重构/任务/开发", trigger_str, file_name) == 10.0
    
    # 2. 分词算分机制：单关键字命中，得分 = 词长
    # "重构" (长度2) => 2.0
    assert _calculate_skill_score("进行代码重构工作", trigger_str, file_name) == 2.0
    # "清理日志" (长度4) => 4.0
    assert _calculate_skill_score("帮我清理日志", trigger_str, file_name) == 4.0
    
    # 3. 组合关联加成：匹配 >= 2 个词时，得分 = 长度和 + 匹配数 * 1.5
    # "重构" (2.0) + "开发" (2.0) + 2 * 1.5 (3.0) = 7.0 分
    assert _calculate_skill_score("重构并进行二次开发", trigger_str, file_name) == 7.0
    
    # 4. 完全不相关过滤 => 0.0 分
    assert _calculate_skill_score("今天午饭吃什么", trigger_str, file_name) == 0.0

def test_calculate_skill_score_fallback():
    """测试 trigger 缺失时的物理文件名降级兜底算分"""
    assert _calculate_skill_score("测试日志分流工作", "", "测试日志分流.md") == 5.0
    assert _calculate_skill_score("测试日志分流工作", None, "测试日志分流.md") == 5.0
    assert _calculate_skill_score("闲聊天气情况", "", "测试日志分流.md") == 0.0

def test_load_core_skills_smart_threshold(mock_skills_setup):
    """方案 A 补强测试：智能动态阈值挂载策略与并发锁验证"""
    # 1. 强语义激活：匹配高度相关技能 (score >= 5.0)，允许全部挂载突破 Top-2 死限制
    prompt_strong = _load_core_skills("我们需要对网关进行开发并且重构清理日志任务")
    assert "临时伞状测试技能" in prompt_strong
    
    # 2. 弱语义激活过滤 (score < 2.0)：完全不相关的一个也不加载
    prompt_chat = _load_core_skills("今天天气不错，出去散步")
    assert prompt_chat == ""
    
    # 3. 并行并发锁验证
    errors = []
    def read_loop_thread():
        try:
            for _ in range(50):
                _load_core_skills("重构清理日志")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)
            
    def write_loop_thread():
        try:
            for _ in range(10):
                with rules_lock:
                    # 模拟深夜进化时的写盘动作
                    time.sleep(0.002)
        except Exception as e:
            errors.append(e)
            
    t1 = threading.Thread(target=read_loop_thread)
    t2 = threading.Thread(target=write_loop_thread)
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    assert len(errors) == 0, f"并发读写时发生多线程死锁或冲突崩溃: {errors}"
