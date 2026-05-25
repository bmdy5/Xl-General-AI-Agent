# Stitch HTML 预览、下载与生成 API 设计规范

## 1. 🎯 设计宗旨

为彻底解决小萤生成的 HTML 页面无法在移动端（手机浏览器、微信、Safari）直接预览并一键另存下载的痛点，同时将 Stitch 生成与提取能力固定为一个外部无状态的微服务接口，供其他网站后端通过 HTTP 快速拉起生成。

---

## 2. 📡 接口详情

大脑主进程常驻的 `8000` 端口 HTTP 服务已自适应绑定 `0.0.0.0`，允许外部及局域网内任意客户端直接连通。

### 2.1 网页一键预览与下载 API

* **端点**：`GET /stitch_latest`
* **Query 参数**：
  * `id` (字符串，可选)：分配给生成任务的专属 UUID。若不传，则默认回读全局根目录下的 [`stitch_latest.html`](file:///Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/stitch_latest.html)。
  * `download` (字符串，可选)：若传入 `true`，接口直接返回二进制文件流，并强挂 HTTP 头 `Content-Disposition: attachment; filename="stitch_latest.html"`，强迫客户端弹出下载窗口。
* **物理路径隔离**：
  * 实装了 UUID 物理隔离以防御多端并发下的“文件互相践踏覆盖”漏洞。
  * 来自 API 任务的文件保存在 `agent/resources/stitch_outputs/<id>.html` 中。
* **UX 悬浮控制台注入**：
  * 在默认预览状态下，接口会自动在 HTML 闭合标签 `</body>` 前动态注入一段精美的、半透明玻璃拟态悬浮控制栏，包含 "⚡ XL Stitch 预览中" 提示以及 "📥 手机下载" 按钮。
  * 悬浮面板交互设计完全采用现代 HSL 柔和高感霓虹绿 `#00ffcc` 与高清晰度模糊（`backdrop-filter: blur(8px)`），体验极佳。

---

### 2.2 外部通用无状态页面生成 API

* **端点**：`POST /api/stitch/generate`
* **Content-Type**：`application/json`
* **请求 Payload**：
  ```json
  {
    "prompt": "Notion 风格极简文档生成器首页，黑色 Logo，居中白色圆角卡片...",
    "style": " Notion 风格",
    "projectId": "9177609784991880809"
  }
  ```
* **高可用防线设计**：
  * **40 秒强熔断**：为防范 Google Cloud REST 接口或 MCP 服务端挂起造成大脑主连接池被长连接慢请求占满，API 内部采用 `asyncio.wait_for` 绑定了最长 40 秒的强熔断保护。
  * **双重容灾自愈**：当接口发生超时或失败时，会自动探测并读取后台可能已经异步下拉落盘成功的物理 HTML，确保最大可用性。
* **标准 JSON 响应**：
  ```json
  {
    "status": "success",
    "id": "76495df0-a764-42b7-a37a-42c23bc9fa12",
    "preview_url": "http://127.0.0.1:8000/stitch_latest?id=76495df0-a764-42b7-a37a-42c23bc9fa12",
    "download_url": "http://127.0.0.1:8000/stitch_latest?id=76495df0-a764-42b7-a37a-42c23bc9fa12&download=true",
    "html": "...(生成的 HTML 源码)..."
  }
  ```

---

## 3. 🧹 24小时磁盘自愈清退任务

大脑主进程启动时，会在后台自主拉起一个无限循环协程 `_disk_cleanup_loop`。每隔 1 小时自动扫描并强行物理删除所有修改时间超过 24 小时的临时 UUID HTML 文件，杜绝服务器磁盘臃肿，彻底消除运维负担。
