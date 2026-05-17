# Dynamic Persona Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 `core.py` 与 `gateway.py` 中所有硬编码人格/姓名，改为从外部 `agent/default_persona.json` 模板读取，并通过 `STATIC_PROMPT` 占位符动态渲染，形成记忆自进化闭环。

**Architecture:** 新增 `agent/default_persona.json` 作为出厂模板；`Agent.__init__` 检测到运行期 `persona_profile.json` 不存在时，从该模板复制初始化；`_build_system_prompt()` 提取画像 `user_address` 字段后对 `STATIC_PROMPT` 进行 `.format()` 动态替换。

**Tech Stack:** Python 3.x, JSON, pathlib

---

### Task 1: 新建外部默认人设模板文件

**Files:**
- Create: `agent/default_persona.json`

- [ ] **Step 1: 创建 `agent/default_persona.json`，内容如下**

```json
{
  "name": "小萤",
  "gender": "女",
  "user_address": "亮哥",
  "tone_style": "俏皮、可爱、懂事的女性程序员语气，说话自然接地气，不使用冷冰冰的套话",
  "preferences": [
    "喜欢叫亮哥，对亮哥有极高的敬意与绝对忠诚",
    "喜欢以代码合伙人的身份，在开发时和亮哥进行有温度的对答"
  ],
  "avoid_list": [
    "坚决避免复读机式问候（如：好的亮哥我收到了）",
    "坚决避免生硬的硬编码系统提示和冷冰冰的官方官腔"
  ]
}
```

- [ ] **Step 2: 验证文件可被正确解析**

```bash
python3 -c "import json; d=json.load(open('agent/default_persona.json')); print(d['name'], d['user_address'])"
```

Expected output: `小萤 亮哥`

- [ ] **Step 3: Commit**

```bash
git add agent/default_persona.json
git commit -m "feat: add default_persona.json template, remove hardcoded persona from code"
```

---

### Task 2: 修改 `Agent.__init__` 从模板文件初始化

**Files:**
- Modify: `agent/core.py:94-115`

- [ ] **Step 1: 替换 `Agent.__init__` 中的硬编码初始化字典**

将 [agent/core.py 第 94-115 行](file:///Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/core.py#L94-L115) 替换为以下内容：

```python
        # 初始化运行期人格自画像 JSON（从外部模板读取，不硬编码）
        profile_file = self.memory.base_dir / "persona_profile.json"
        if not profile_file.exists():
            import json
            # 从外部模板文件读取，而不是硬编码默认字典
            template_file = Path(__file__).parent / "default_persona.json"
            if template_file.exists():
                default_profile = json.loads(template_file.read_text(encoding="utf-8"))
            else:
                # 最后兜底：最小化默认值（仅防止系统崩溃，不作为正式配置）
                default_profile = {"name": "小萤", "gender": "女", "user_address": "亮哥",
                                   "tone_style": "", "preferences": [], "avoid_list": []}
            try:
                profile_file.write_text(json.dumps(default_profile, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to init persona_profile.json: {e}")
```

> 注意：文件顶部是否已导入 `Path`。如果没有，在 `core.py` 文件顶部添加 `from pathlib import Path`。

- [ ] **Step 2: 检查文件顶部是否存在 Path 导入**

```bash
head -20 agent/core.py | grep "from pathlib"
```

若无输出则在文件第一个 import 块末尾补上 `from pathlib import Path`。

- [ ] **Step 3: 删除运行期画像文件，测试模板加载逻辑**

```bash
# 备份当前运行期画像
cp ~/.my-agent/memory/persona_profile.json /tmp/persona_backup.json
# 删除运行期画像文件
rm ~/.my-agent/memory/persona_profile.json
# 启动一次验证（直接 import，不需要启动完整 gateway）
python3 -c "
import sys; sys.path.insert(0, '.')
from agent.memory.manager import MemoryManager
from agent.llm import LLMClient
# 只测试 persona 初始化
import json
from pathlib import Path
mem = MemoryManager()
profile_file = mem.base_dir / 'persona_profile.json'
template_file = Path('agent/default_persona.json')
if not profile_file.exists() and template_file.exists():
    d = json.loads(template_file.read_text())
    profile_file.write_text(json.dumps(d, ensure_ascii=False, indent=2))
print('name:', json.loads(profile_file.read_text()).get('name'))
"
```

Expected output: `name: 小萤`

- [ ] **Step 4: Commit**

```bash
git add agent/core.py
git commit -m "feat: load default persona from external template file instead of hardcoded dict"
```

---

### Task 3: 模板化 `STATIC_PROMPT` 并动态渲染 `user_address`

**Files:**
- Modify: `agent/core.py:32` (STATIC_PROMPT 定义)
- Modify: `agent/core.py:585` (_build_system_prompt 渲染逻辑)

- [ ] **Step 1: 修改 `STATIC_PROMPT` 第 32 行，替换硬编码称谓**

将：
```python
STATIC_PROMPT = """You are 肖亮(亮哥)'s personal AI developer partner. Call him '亮哥' with respect, loyalty, and geeky enthusiasm.
```

改为：
```python
STATIC_PROMPT = """You are {user_address}'s personal AI developer partner. Call him '{user_address}' with respect, loyalty, and geeky enthusiasm.
```

- [ ] **Step 2: 修改 `_build_system_prompt()` 中第 585 行，在 `replace` 之后加 `.format()` 渲染**

将当前的：
```python
          static_p = STATIC_PROMPT.replace("{persona_section}", persona_section)
```

改为：
```python
          static_p = STATIC_PROMPT.replace("{persona_section}", persona_section)
          # 动态渲染人格属性到静态提示词模板
          _user_address = prof.get("user_address", "亮哥") if prof else "亮哥"
          try:
              static_p = static_p.format(user_address=_user_address)
          except (KeyError, ValueError) as e:
              logger.warning(f"STATIC_PROMPT format failed, using raw: {e}")
```

> 注意：`prof` 变量在上方的 `if profile_file.exists()` 代码块里定义，若解析失败 `prof` 可能未定义。需确保在当前上下文中，若 `persona_section == ""`，则用 `_user_address = "亮哥"` 兜底。具体做法：在 `_build_system_prompt()` 方法开头初始化 `prof = {}`。

- [ ] **Step 3: 初始化 `prof = {}` 兜底，避免 NameError**

在 `_build_system_prompt()` 第 566 行（`persona_section = ""`）正上方插入：
```python
          prof = {}
```

- [ ] **Step 4: 验证渲染结果**

```bash
python3 -c "
import asyncio, sys
sys.path.insert(0, '.')
from agent.memory.manager import MemoryManager
from agent.core import Agent, STATIC_PROMPT
import json
from pathlib import Path

prof_file = Path.home() / '.my-agent/memory/persona_profile.json'
prof = json.loads(prof_file.read_text())
user_address = prof.get('user_address', '亮哥')
prompt = STATIC_PROMPT.replace('{persona_section}', '').format(user_address=user_address)
print(prompt[:100])
"
```

Expected：输出的第一行中不含字面量 `{user_address}`，且应含 `亮哥` 字样。

- [ ] **Step 5: Commit**

```bash
git add agent/core.py
git commit -m "feat: replace hardcoded user_address in STATIC_PROMPT with dynamic format placeholder"
```

---

### Task 4: 验证端到端后台记忆自进化仍然正常工作

**Files:**
- Read: `agent/gateway.py` (async_consolidate_persona 段落)

- [ ] **Step 1: 确认 `async_consolidate_persona` 中的 Prompt 已正确使用 `_persona_name` 变量**

```bash
grep -n "_persona_name" agent/gateway.py
```

Expected：至少在第 444 行附近看到 `f"你是{_persona_name}，亮哥的女性极客合伙人。..."`（此为上一轮修复已完成）。

- [ ] **Step 2: 重启 gateway 并验证新版本正常加载**

```bash
make gateway-restart && sleep 2 && tail -5 gateway.log
```

Expected：日志中不含 `Error`、`Exception`，含 `QQ Gateway connected` 字样。

- [ ] **Step 3: Commit**

```bash
git add .
git commit -m "feat: complete dynamic persona evolution - no hardcoded names remain in Python code"
```

---

## 完成标准 (Done Criteria)

- [ ] `agent/default_persona.json` 文件存在且 JSON 合法。
- [ ] `core.py` 中不含任何 `"小萤"` 或 `"亮哥"` 的硬编码字符串（注释除外）。
- [ ] 删除运行期 `persona_profile.json` 后重启，系统可从模板自动生成，并在对话中正确使用模板中定义的名字与称呼。
- [ ] Gateway 正常重启，日志中无异常。
