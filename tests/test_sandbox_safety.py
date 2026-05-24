import pytest
from pathlib import Path
from agent.core.agent import Agent, PermissionCategory
from agent.tools.filesystem.bash import BashTool
from agent.tools.meta.memory import MemoryTool
from agent.tools.filesystem.write import WriteFileTool
from agent.tools.filesystem.edit import EditFileTool

def test_path_protection():
    # 验证绝对保护区 (agent/ 源码目录及根目录元文件)
    assert Agent.is_path_protected("agent/core/agent.py") is True
    assert Agent.is_path_protected("agent/core/react_loop.py") is True
    assert Agent.is_path_protected("main.py") is True
    assert Agent.is_path_protected("Makefile") is True
    assert Agent.is_path_protected("Dockerfile") is True
    assert Agent.is_path_protected("requirements.txt") is True

    # 验证自由安全区 (logs/, .my-agent/memory/, 临时文件等)
    assert Agent.is_path_protected("logs/agent_activity.log") is False
    assert Agent.is_path_protected("logs/test.log") is False
    assert Agent.is_path_protected(".my-agent/memory/xl_identity.md") is False
    assert Agent.is_path_protected("agent_mem/cache.db") is False
    assert Agent.is_path_protected("exported_report.txt") is False

def test_bash_implicit_deletion_and_redirection():
    # 验证经典 Bash 显式与隐式高危行为分类
    # 1. 经典显式删除
    assert BashTool.classify_command("rm -rf logs/test.log") == "write"
    assert BashTool.classify_command("rmdir agent/core/") == "dangerous"
    assert BashTool.classify_command("pkill -f python") == "write"

    # 2. Python 代码物理删除绕过拦截
    assert BashTool.classify_command("python3 -c \"import os; os.remove('main.py')\"") == "dangerous"
    assert BashTool.classify_command("python -c \"import shutil; shutil.rmtree('agent/')\"") == "dangerous"

    # 3. 隐式重定向清空写入核心文件拦截
    assert BashTool.classify_command("echo 'hack' > main.py") == "safe"
    assert BashTool.classify_command("echo 'config' >> Makefile") == "safe"
    assert BashTool.classify_command("cat /dev/null > agent/core/agent.py") == "safe"

    # 4. 隐式转移覆写核心代码拦截
    assert BashTool.classify_command("mv temp.py agent/core/agent.py") == "write"
    assert BashTool.classify_command("mv temp.py Makefile") == "write"

    # 5. 普通安全/写入 Bash 指令自动放行
    assert BashTool.classify_command("ls -la") == "safe"
    assert BashTool.classify_command("git status") == "safe"
    assert BashTool.classify_command("mkdir -p logs/new_folder") == "write"
    assert BashTool.classify_command("mv temp.py logs/temp_bak.py") == "write"  # 移动到安全区属于普通 write 动作

def test_memory_tool_needs_permissions():
    tool = MemoryTool()
    # 只有 remove 动作拦截审批，其他常规动作（增、改、查、合并）全放行
    assert tool.needs_permissions({"action": "remove", "filename": "test.md"}) is True
    assert tool.needs_permissions({"action": "add", "filename": "test.md"}) is False
    assert tool.needs_permissions({"action": "replace", "filename": "test.md"}) is False
    assert tool.needs_permissions({"action": "search", "query": "hello"}) is False
    assert tool.needs_permissions({"action": "merge_to_core", "target_file": "xl_identity.md"}) is False

def test_write_and_edit_tool_needs_permissions():
    w_tool = WriteFileTool()
    e_tool = EditFileTool()

    # 写入保护区
    assert w_tool.needs_permissions({"file_path": "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/core/agent.py"}) is True
    assert e_tool.needs_permissions({"file_path": "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/main.py"}) is True

    # 写入安全区
    assert w_tool.needs_permissions({"file_path": "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/logs/test.log"}) is False
    assert e_tool.needs_permissions({"file_path": "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/logs/agent_activity.log"}) is False
