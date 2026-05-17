# QQ Gateway 7x24小时常驻与崩溃自愈系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 QQ Gateway 进程在 Mac 系统开机时自启且 7×24 小时常驻保活，并在 WebSocket 崩溃断连 10 次时自动重启 Docker 容器自愈，在 QQ 登录态失效时弹出 Mac 桌面原生横幅声效警报。

**Architecture:** 
1. 编写 plist 文件托管于 macOS LaunchAgents 保证 Gateway 进程前台自启与 KeepAlive 保活。
2. 在 `QQGateway.run()` 异常分支中拦截断连计数，连续失败 10 次时调用 `asyncio.create_subprocess_shell` 强行执行 `docker restart napcat` 重启容器。
3. 在 `_daemon_loop` 中定时轮询 `/get_login_info`，登录失效时利用 AppleScript 触发 macOS 通知，并配置 30 分钟防刷屏冷却时间。

**Tech Stack:** Python 3, asyncio, aiohttp, macOS AppleScript, macOS launchd (plist)

---

### Task 1: 创建 macOS launchd 守护配置文件

**Files:**
- Create: `com.myagent.qqgateway.plist`

- [ ] **Step 1: 编写 plist 配置文件**

在项目根目录下新建 `com.myagent.qqgateway.plist`，写入以下标准托管格式：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.myagent.qqgateway</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/.venv/bin/python</string>
        <string>main.py</string>
        <string>--gateway</string>
    </array>
    <key>StandardOutPath</key>
    <string>/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/gateway.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/gateway.err</string>
    <key>WorkingDirectory</key>
    <string>/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>/Users/xiaofeng</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

- [ ] **Step 2: 验证 plist 的 XML 语法正确性**

运行以下 Mac 语法自检命令：
`plutil -lint com.myagent.qqgateway.plist`
Expected output: `com.myagent.qqgateway.plist: OK`

- [ ] **Step 3: 提交代码**

```bash
git add com.myagent.qqgateway.plist
git commit -m "chore: add macOS launchd plist configuration for qqgateway"
```

---

### Task 2: 实装 WebSocket 断连 Docker 自愈重启机制

**Files:**
- Modify: `agent/gateway.py:40-131`

- [ ] **Step 1: 在 `QQGateway.__init__` 中初始化失败计数器**

修改 `agent/gateway.py` 中的 `__init__`：

```python
    def __init__(self, agent_factory):
        self._factory = agent_factory          # () → Agent
        self._agents: dict[str, object] = {}   # user_id/group_id → Agent
        self._http: Optional[aiohttp.ClientSession] = None
        self._pending_perms: dict[str, object] = {}  # session_key → _PermEvent
        self._reconnect_failures: int = 0      # 连续断连计数器
```

- [ ] **Step 2: 实装 WS 连接成功时的计数归零与重连失败时的 Docker 重启自愈**

修改 `gateway.py` 的 `run` 和 `_ws_loop` 方法以记录重连次数。
在 `_ws_loop` 中，成功连接后重置计数器：

```python
            async with ws_session.ws_connect(NC_WS_URL, headers=headers) as ws:
                logger.info(f"QQ Gateway connected: {NC_WS_URL}")
                self._reconnect_failures = 0  # 成功握手，计数器归零
```

在 `run` 的重连捕获分支中累加计数器，并判断是否触发物理自愈：

```python
    async def run(self):
        """连接 NapCat WebSocket，循环处理消息."""
        async with aiohttp.ClientSession() as http:
            self._http = http
            # 开启后台守护巡检线程
            asyncio.create_task(self._daemon_loop())
            while True:
                try:
                    await self._ws_loop()
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                    self._reconnect_failures += 1
                    logger.warning(f"WebSocket disconnected (Count: {self._reconnect_failures}/10): {e}, retry in 5s...")
                    
                    if self._reconnect_failures >= 10:
                        logger.error("WebSocket disconnected 10 times consecutively. Triggering NapCat self-healing restart...")
                        self._reconnect_failures = 0
                        try:
                            # 异步执行 Docker 重启指令
                            proc = await asyncio.create_subprocess_shell("docker restart napcat")
                            await proc.wait()
                            logger.info("NapCat container restarted successfully. Waiting 10s for initialization...")
                        except Exception as restart_err:
                            logger.error(f"Failed to restart NapCat container: {restart_err}")
                        await asyncio.sleep(10)  # 给 Docker 启动腾出 10 秒钟缓冲时间
                    else:
                        await asyncio.sleep(5)
```

- [ ] **Step 3: 运行静态语法检查**

运行：`python -m py_compile agent/gateway.py`
Expected output: 零报错退出

- [ ] **Step 4: 提交代码**

```bash
git add agent/gateway.py
git commit -m "feat(gateway): implement websocket reconnection failure docker self-healing"
```

---

### Task 3: 实装 QQ 登录态失效 Mac 横幅声效警报

**Files:**
- Modify: `agent/gateway.py:40-131`

- [ ] **Step 1: 在 `QQGateway.__init__` 中初始化冷却时间戳**

修改 `agent/gateway.py` 的 `__init__` 函数，添加上一次报警时间戳：

```python
    def __init__(self, agent_factory):
        self._factory = agent_factory          # () → Agent
        self._agents: dict[str, object] = {}   # user_id/group_id → Agent
        self._http: Optional[aiohttp.ClientSession] = None
        self._pending_perms: dict[str, object] = {}  # session_key → _PermEvent
        self._reconnect_failures: int = 0      # 连续断连计数器
        self._last_offline_alert: float = 0.0  # 上次掉线报警时间戳（冷却防骚扰）
```

- [ ] **Step 2: 修改 `_daemon_loop`，嵌入健康检测与 macOS 横幅报警机制**

修改 `agent/gateway.py` 中的 `_daemon_loop`。在 `while True` 的大循环中，轮询心跳前对 `/get_login_info` 状态做健康审计：

```python
    async def _daemon_loop(self):
        """后台守护巡检线程：定时检测到期任务，向管理员 QQ 推送确认并安全执行"""
        from agent.task_queue import TaskQueue
        import time
        logger.info("QQ Gateway Background Daemon Loop started.")
        q = TaskQueue()
        
        admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
        if not admin_id:
            logger.warning("QQ_ADMIN_ID not configured in .env. Background daemon is disabled.")
            return

        session_key = f"user_{admin_id}"

        while True:
            # 必须等 NapCat HTTP 连接就绪后才开始工作，避免 self._http 未就绪引发报错
            if not self._http:
                await asyncio.sleep(10)
                continue

            # ── 1. QQ 登录态主动感知与 macOS 警告环 ────────────────────────────────────
            try:
                # 请求 OneBot 状态接口
                url = f"{NC_HTTP_URL}/get_login_info"
                headers = {}
                if NC_TOKEN:
                    headers["Authorization"] = f"Bearer {NC_TOKEN}"
                
                async with self._http.get(url, headers=headers) as resp:
                    is_online = False
                    if resp.status == 200:
                        res_data = await resp.json()
                        if res_data.get("status") == "ok":
                            is_online = res_data.get("data", {}).get("online", False)
                    
                    if not is_online:
                        # 触发登录失效警报
                        current_time = time.time()
                        if current_time - self._last_offline_alert > 1800:  # 30分钟防刷冷却
                            self._last_offline_alert = current_time
                            logger.error("QQ Login Session expired! Triggering macOS native alert notification...")
                            # 唤起 macOS 原生 AppleScript 横幅弹窗，附带 Glass 声效
                            alert_cmd = (
                                'osascript -e \'display notification "QQ 机器人登录态已过期，请点击 WebUI 重新扫码登录！" '
                                'with title "⚠️ XL Agent 掉线警报" sound name "Glass"\''
                            )
                            proc = await asyncio.create_subprocess_shell(alert_cmd)
                            await proc.wait()
            except Exception as check_err:
                logger.warning(f"Failed to check QQ login status: {check_err}")

            # ── 2. 定时任务轮询逻辑 ──────────────────────────────────────────────────
            try:
                due_tasks = q.process_due()
                for task in due_tasks:
                    task_id = task["id"]
                    desc = task["description"]
                    action = task["action"]

                    # 1. 向管理员 QQ 私聊推送确认请求
                    await self._send("private", admin_id, "", 
                        f"⏰ [全天候中枢巡检]\n亮哥，检测到后台任务到期：\n【{desc}】\n\n回复「允许」或「y」授权我立即执行，回复其他取消。")

                    # 2. 注册等待锁，阻止线程并挂起 5 分钟等待用户在 QQ 上的答复
                    evt = _PermEvent()
                    self._pending_perms[session_key] = evt
                    try:
                        await asyncio.wait_for(evt.wait(), timeout=300)
                        approved = evt.result
                    except asyncio.TimeoutError:
                        approved = False
                    finally:
                        self._pending_perms.pop(session_key, None)

                    # 3. 根据主人确认结果进行后台调度
                    if approved:
                        await self._send("private", admin_id, "", f"🚀 正在后台执行任务: {desc}...")
                        agent = self._factory()
                        buf = ""
                        try:
                            async for evt in agent.run(action, stream=True):
                                if evt["type"] == "text_delta":
                                    buf += evt["content"]
                                elif evt["type"] == "permission_request":
                                    agent.approve_permission()
                                elif evt["type"] == "error":
                                    buf += f"\n[错误: {evt['content']}]"
                        except Exception as e:
                            buf += f"\n[异常: {e}]"

                        q.mark_done(task_id)

                        result_msg = f"✅ [执行完成]\n任务：{desc}\n\n执行结果反馈：\n{buf.strip()[:1500]}"
                        await self._send("private", admin_id, "", result_msg)
                    else:
                        await self._send("private", admin_id, "", f"⏸️ 已跳过任务：{desc}")

            except Exception as e:
                logger.error(f"Daemon loop encountered an error: {e}")

            # 每 5 分钟轮询一次
            await asyncio.sleep(300)
```

- [ ] **Step 3: 运行语法与静态校验**

运行：`python -m py_compile agent/gateway.py`
Expected output: 零报错退出

- [ ] **Step 4: 提交代码**

```bash
git add agent/gateway.py
git commit -m "feat(gateway): implement active QQ login status check and macOS alert notification"
```
