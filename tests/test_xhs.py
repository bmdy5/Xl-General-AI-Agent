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

    print("\n=== Step 4: 测试发布图文笔记 (Publish) ===")
    test_image_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../agent/ui/dashboard_v2/assets/gen_Square_avatar_of_an_adorable_y.png"))
    print(f"待上传的测试图片路径: '{test_image_path}'")
    if not os.path.exists(test_image_path):
        print(f"警告: 测试图片 {test_image_path} 不存在，尝试使用项目根目录下的备用图片。")
        test_image_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../qrcode_login.png"))
    
    publish_args = {
        "action": "publish",
        "title": "小萤 Agent 连通性测试",
        "content": "这是一条由我的自搭建 AI Agent「小萤」自动调起后台 Go MCP 服务所发布的图文测试。全流程握手和读写均已完美通畅！",
        "image_paths": [test_image_path],
        "tags": ["AI", "Agent", "测试"]
    }
    
    print("开始调用发布接口...")
    async for progress in tool.call(publish_args):
        if progress.type == "progress":
            print(f"[进度] {progress.data}")
        elif progress.type == "result":
            print("\n[发布结果]:")
            print(progress.data)

    print("\n=== 测试流程全部结束 ===")


if __name__ == "__main__":
    asyncio.run(run_tests())
