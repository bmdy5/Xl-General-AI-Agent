import os
import pytest
import shutil
from pathlib import Path
from unittest.mock import patch
from agent.memory.manager import MemoryManager
from agent.memory.context import search_notes
from agent.memory.store import update_knowledge_index

@pytest.mark.asyncio
async def test_resolve_adaptive_path():
    """验证 MemoryManager 的路径自适应展开功能"""
    # 模拟 MemoryManager
    manager = MemoryManager()
    
    # 1. 验证相对路径自愈定位：./ 开头应该定位在项目根目录下
    proj_root = Path(__file__).resolve().parents[1]
    rel_path_str = "./.memory_temp_test"
    resolved = manager.resolve_adaptive_path(rel_path_str)
    assert resolved == (proj_root / ".memory_temp_test").resolve()
    
    # 2. 验证 Home 目录自愈展开：~ 开头应该展开为用户 Home 目录
    home_path_str = "~/.my-agent_temp_test"
    resolved_home = manager.resolve_adaptive_path(home_path_str)
    assert resolved_home == Path(os.path.expanduser(home_path_str)).resolve()


@pytest.mark.asyncio
async def test_multi_instance_isolation():
    """验证多实例哈希隔离机制，防止 WAL 锁冲突"""
    # 1. Mock 环境变量及 Settings 以验证 admin_id 对路径的隔离
    test_settings = {
        "security": {"admin_id": "999999999"},
        "memory": {
            "base_dir": "./.memory_test_temp",
            "multi_instance_isolation": True
        }
    }
    
    with patch("agent.core.config.settings.get", side_effect=lambda k, default=None: test_settings.get(k, default)):
        with patch.dict(os.environ, {"QQ_ADMIN_ID": "999999999"}):
            manager_1 = MemoryManager()
            # 验证 manager_1 最终的主目录和备份目录是否都包含了 999999999 哈希隔离子目录
            assert "999999999" in str(manager_1.base_dir)
            assert "999999999" in str(manager_1.backup_dir)
            
            # 清理生成的临时目录
            if manager_1.base_dir.exists():
                shutil.rmtree(manager_1.base_dir.parent)
            if manager_1.backup_dir.exists():
                shutil.rmtree(manager_1.backup_dir.parent)


@pytest.mark.asyncio
async def test_notes_path_empty_and_healing():
    """验证增量学习笔记同步时非空路径和防空自愈"""
    test_settings = {
        "knowledge_base": {
            "notes_paths": ["/non_existent_directory_xxx/Desktop/invalid_path_1", "/another_invalid_path_2"],
            "kb_dir": "/non_existent_kb_dir_xxx/agent自主学习的东西"
        }
    }
    
    # 模拟 manager，确保 base_dir 是个可控目录
    temp_memory_dir = "/tmp/test_agent_notes_healing"
    if os.path.exists(temp_memory_dir):
        shutil.rmtree(temp_memory_dir)
    os.makedirs(temp_memory_dir)
    
    try:
        manager = MemoryManager(base_dir=temp_memory_dir)
        
        with patch("agent.core.config.settings.get", side_effect=lambda k, default=None: test_settings.get(k, default)):
            # 1. 验证 search_notes 在路径不存在时，不会抛错，且不产生任何多余垃圾文件夹
            res = search_notes(manager, "测试查询")
            # 应该由于没有匹配的内容而返回空列表，但整个自愈机制不抛异常
            assert res == []
            
            # 验证确实没有在桌面上强行生成空文件夹
            assert not os.path.exists("/non_existent_directory_xxx/Desktop/invalid_path_1")
            assert not os.path.exists("/another_invalid_path_2")
            
            # 2. 验证 update_knowledge_index 遇到 KB_DIR 不存在时，静默捕获不抛异常且安全跳过
            update_knowledge_index("knowledge", "test_entry")
            # 应该没有抛错，也没有在此不存在的路径下强制生成文件夹
            assert not os.path.exists("/non_existent_kb_dir_xxx/agent自主学习的东西")
            
    finally:
        if os.path.exists(temp_memory_dir):
            shutil.rmtree(temp_memory_dir)
