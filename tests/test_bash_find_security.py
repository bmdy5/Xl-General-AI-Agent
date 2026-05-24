import pytest
from pathlib import Path
from agent.tools.filesystem.bash import BashTool
from agent.tools.base_tool import ToolResult

@pytest.mark.asyncio
async def test_bash_find_security_interception():
    # 模拟项目根路径环境
    tool = BashTool()
    
    # 1. 测试项目内部的 find，不带 -maxdepth，应当【放行】（不拦截，进入 subprocess 执行或返回其他结果）
    # 由于本地并不一定真的在执行（没有真的调用 shell 执行完），我们通过调用 call() 并检查返回的 generator 的输出。
    # 我们可以通过 mock 执行环境或直接检查 call 生成器的第一个 yield 的类型来确定是否被拦截。
    project_dir = str(Path(__file__).resolve().parents[1].resolve())
    
    # 执行安全 find（项目内）
    gen_safe = tool.call({"command": f"find {project_dir} -name '*.py'"})
    results_safe = []
    async for res in gen_safe:
        results_safe.append(res)
    # 如果没被拦截，应该直接进入正常的执行逻辑（可能会报命令输出或 exit code，而不是包含 "[行为拦截提示]" 的提示）
    assert len(results_safe) > 0
    assert "[行为拦截提示]" not in results_safe[0].data

    # 2. 测试大范围全盘 find，且不带 -maxdepth，应当【拦截】并返回行为拦截提示
    gen_unsafe = tool.call({"command": "find /Users/xiaofeng -name '*.png'"})
    results_unsafe = []
    async for res in gen_unsafe:
        results_unsafe.append(res)
    assert len(results_unsafe) == 1
    assert "[行为拦截提示]" in results_unsafe[0].data

    # 3. 测试大范围全盘 find，但带了 -maxdepth，应当【放行】
    # 为了避免真实执行慢，我们使用一个不存在的目录或带 maxdepth 的 find。
    # 注意，即使真实执行，带了 maxdepth 也不会卡死。
    gen_with_depth = tool.call({"command": "find /Users/xiaofeng -maxdepth 2 -name '*.png'"})
    results_with_depth = []
    async for res in gen_with_depth:
        results_with_depth.append(res)
    assert len(results_with_depth) > 0
    assert "[行为拦截提示]" not in results_with_depth[0].data

    # 4. 测试多语句 find 绕过（如其中一个在大范围且无 -maxdepth，另一个在项目内）
    gen_multi = tool.call({"command": f"find {project_dir} -name '*.py'; find /Users/xiaofeng/Desktop -name '*.png'"})
    results_multi = []
    async for res in gen_multi:
        results_multi.append(res)
    assert len(results_multi) == 1
    assert "[行为拦截提示]" in results_multi[0].data
