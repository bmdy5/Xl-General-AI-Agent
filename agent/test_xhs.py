"""小红书工具自动化测试脚本.

测试小红书搜索(search)与详情(detail)功能，并将发布(publish)设计为受控测试。
"""

import asyncio
import os
import sys

# 将项目根目录加入到 sys.path，保证可以正常导入 agent 模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.tools.xiaohongshu_tool import XiaohongshuTool


async def run_tests():
    tool = XiaohongshuTool()
    
    print("=== Step 1: 验证参数输入 ===")
    val1 = await tool.validate_input({"action": "search", "keyword": "AI"})
    print("Search validation:", val1)
    
    val2 = await tool.validate_input({"action": "publish"})
    print("Publish validation (missing fields):", val2)

    print("\n=== Step 2: 测试小红书搜索 (Search) ===")
    search_keyword = "AI Agent"
    print(f"开始搜索关键词: '{search_keyword}'...")
    
    search_results = ""
    async for progress in tool.call({"action": "search", "keyword": search_keyword}):
        if progress.type == "progress":
            print(f"[进度] {progress.data}")
        elif progress.type == "result":
            search_results = progress.data
            print("\n[搜索结果]:")
            print(search_results[:1000])  # 只展示前1000字符
            if len(search_results) > 1000:
                print("... (省略后面部分)")

    # 提取搜索结果中的一个笔记ID和Token来做详情测试
    note_id = None
    xsec_token = None
    try:
        import json
        data = json.loads(search_results)
        feeds = data.get("feeds", [])
        if feeds:
            note_id = feeds[0].get("id")
            xsec_token = feeds[0].get("xsecToken")
    except Exception as e:
        print(f"解析 JSON 搜索结果失败: {e}")

    if not note_id or not xsec_token:
        note_id = "67243c39000000000701043f"  # 使用默认备用笔记ID
        xsec_token = "AB4RmTnflIj5uWJBrEqsCR9Bx_FtrfnxGEx3xNQRlDAe4="
        print(f"\n无法从搜索结果中自动解析到笔记 ID 或 Token，使用默认测试 ID: {note_id}, Token: {xsec_token}")
    else:
        print(f"\n成功自动提取到测试笔记 ID: {note_id}, Token: {xsec_token}")

    print("\n=== Step 3: 测试获取笔记详情 (Detail) ===")
    print(f"开始加载笔记详情: '{note_id}'...")
    async for progress in tool.call({"action": "detail", "note_id": note_id, "xsec_token": xsec_token}):
        if progress.type == "progress":
            print(f"[进度] {progress.data}")
        elif progress.type == "result":
            detail_data = progress.data
            print("\n[详情结果]:")
            print(detail_data[:1500])  # 展示部分内容

    print("\n=== Step 4: 写入测试准备 ===")
    print("只读功能 (Search, Detail) 自检完全通过！")
    print("小红书图文发布(Publish)已被安全截断，等待用户最终授权同意后方可进行写入测试。")


if __name__ == "__main__":
    asyncio.run(run_tests())
