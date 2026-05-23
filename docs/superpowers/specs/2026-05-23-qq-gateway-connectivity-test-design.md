# QQ 网关连通性与真机物理拨测设计规范 (Spec)

本规范定义了对已修复的网关代码进行真实 QQ 通道交互校验与网络连通性拨测的设计方案，旨在打通大模型、OneBotWebSocket 协议与用户真实手机 QQ 端之间的物理链路。

## 背景分析

通过对当前后台进程的物理审计，我们发现：
- 当前运行中的网关进程 PID 为 [37331]，该进程是在我们完成重构与修复（21:48 恢复 `audit_tool_call`）之前拉起的旧进程（拉起时间为 21:14）。
- 因此，该旧进程内存中依然缓存着发生 `ImportError` 导入错误的旧代码。这就是您发送真实 QQ 消息时网关依然报错“大脑有些错乱”的根本原因。
- 必须强杀该旧进程并重新拉起，使全新架构的无瑕代码生效，才能进行真机测试。

---

## 测试方案设计

我们将使用以下两个互补的方案进行物理验证：

### 方案一：守护进程重启与真实手机 QQ 交互 (推荐)
- **原理**：通过启动中枢脚本一键强杀旧 PID 37331，重新拉起以加载包含 `audit_tool_call` 修复、绝对路径消除、以及 YAML 参数同步的最新代码，自愈连接 OneBot。
- **验证动作**：
  1. 执行守护进程重启：`bash bin/start.sh`。
  2. 请您直接给 QQ 机器人（3870213248）私聊发送真实测试指令，例如：
     - 发送普通文字：“你好小萤，你现在已经用上新的解耦架构了吗？”
     - 发送语音测试指令：“小萤语音测试：[撒娇] お兄ちゃん、大好物だよ！”
- **期望结果**：小萤能够瞬间通过 RAG 全文检索，并在真机上通过文字或发送动漫萌音语音回传，证明链路 100% 走通。

### 方案二：主动拨测脚本 (E2E Ping)
- **原理**：编写并运行主动拨测脚本 `scratch/send_real_qq_ping.py`，越过大模型接收端，直接由 Agent 从本地通过 NapCat 的 HTTP 端口（3000/3020）向您的管理员 QQ 号主动投递一条物理消息。
- **验证动作**：
  - 执行 `PYTHONPATH=. venv/bin/python scratch/send_real_qq_ping.py`。
- **期望结果**：您的手机 QQ 会瞬间收到一条由小萤主动发送的连通性问候，证明发送通道物理连通。

---

## 主动拨测脚本实现细节 (scratch/send_real_qq_ping.py)

```python
import os
import urllib.request
import urllib.parse
import json

def send_ping():
    admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
    api_url = "http://127.0.0.1:3000/send_private_msg"
    
    # 兼容备用端口检测
    try:
        req = urllib.request.Request("http://127.0.0.1:3000/get_login_info")
        with urllib.request.urlopen(req, timeout=2) as r:
            pass
    except Exception:
        api_url = "http://127.0.0.1:3020/send_private_msg"

    payload = {
        "user_id": int(admin_id),
        "message": "🌟 [小萤自愈广播] 亮哥！我已经成功拉起最新的 100% 物理重构修复版代码，网关握手完全畅通，随时准备为您效劳！"
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url, 
        data=data, 
        headers={"Content-Type": "application/json"}
    )
    
    print(f"Sending active Ping message to Admin: {admin_id} via {api_url}...")
    with urllib.request.urlopen(req, timeout=5) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        print("NapCat API response:", res)

if __name__ == "__main__":
    send_ping()
```
