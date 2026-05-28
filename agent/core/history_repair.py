import logging

logger = logging.getLogger("agent.history_repair")

from ..memory.error_tracker import ERROR_INDICATORS

async def repair_history(agent) -> None:
    """双向修复：补全缺失的 tool 结果 + 删除孤立的 tool 消息 + 智能重排交错的工具响应."""
    if not agent.messages:
        return

    # 脏标记：消息数未变则跳过全量扫描
    msg_count = len(agent.messages)
    last_count = getattr(agent, "_repair_last_count", 0)
    if msg_count == last_count:
        return
    agent._repair_last_count = msg_count

    repair_logger = logging.getLogger("agent.repair")

    # 0. 智能重排交错的工具响应与用户/系统消息
    reordered_messages = []
    i = 0
    n = len(agent.messages)
    has_reordered = False
    while i < n:
        msg = agent.messages[i]
        reordered_messages.append(msg)
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tc_ids = [tc.get("id") for tc in msg["tool_calls"] if tc.get("id")]
            if tc_ids:
                matching_tools = []
                found_indices = []
                has_interleaved = False
                first_tool_idx = -1
                last_tool_idx = -1
                
                for j in range(i + 1, n):
                    m_later = agent.messages[j]
                    if m_later.get("role") == "tool" and m_later.get("tool_call_id") in tc_ids:
                        matching_tools.append(m_later)
                        found_indices.append(j)
                        if first_tool_idx == -1:
                            first_tool_idx = j
                        last_tool_idx = j
                
                if matching_tools:
                    for idx_between in range(i + 1, last_tool_idx):
                        m_bet = agent.messages[idx_between]
                        if m_bet.get("role") != "tool" or m_bet.get("tool_call_id") not in tc_ids:
                            has_interleaved = True
                            break
                
                if has_interleaved:
                    id_to_tool = {m["tool_call_id"]: m for m in matching_tools}
                    sorted_tools = [id_to_tool[tid] for tid in tc_ids if tid in id_to_tool]
                    
                    reordered_messages.extend(sorted_tools)
                    agent.messages = [m for idx, m in enumerate(agent.messages) if idx not in found_indices]
                    n = len(agent.messages)
                    has_reordered = True
                    repair_logger.warning(
                        f"智能重排：修复了 {len(sorted_tools)} 个被用户/系统消息交错夹杂的工具响应消息"
                    )
        i += 1
    
    if has_reordered:
        agent.messages = reordered_messages

    # 1. 扫描所有 assistant 发出的 tool_call_ids
    assistant_tc_ids = set()
    for m in agent.messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if tc.get("id"):
                    assistant_tc_ids.add(tc["id"])

    # 2. 扫描所有 tool 消息
    tool_msgs = [(idx, m) for idx, m in enumerate(agent.messages)
                 if m.get("role") == "tool" and m.get("tool_call_id")]

    # 3. 删除孤立的 tool 消息
    orphan_tools = [(idx, m) for idx, m in tool_msgs
                    if m["tool_call_id"] not in assistant_tc_ids]
    if orphan_tools:
        for idx, m in reversed(orphan_tools):
            del agent.messages[idx]
        repair_logger.warning(
            f"Transcript repair: removed {len(orphan_tools)} orphan tool messages"
        )

    # 4. 补全缺失的 tool 结果
    existing_tool_ids = {m["tool_call_id"] for _, m in tool_msgs}
    missing = [tc_id for tc_id in assistant_tc_ids if tc_id not in existing_tool_ids]

    if missing:
        repair_logger.warning(f"检测到 {len(missing)} 个孤儿工具调用，正在自动补全占位符...")
        for tc_id in missing:
            assistant_idx = -1
            tool_name = "unknown"
            for idx, m in enumerate(agent.messages):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        if tc.get("id") == tc_id:
                            assistant_idx = idx
                            tool_name = tc.get("function", {}).get("name", "unknown")
                            break
                    if assistant_idx != -1:
                        break

            if assistant_idx != -1:
                insert_idx = assistant_idx + 1
                while insert_idx < len(agent.messages) and agent.messages[insert_idx].get("role") == "tool":
                    insert_idx += 1
                
                placeholder = {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": tool_name,
                    "content": "已恢复执行"
                }
                agent.messages.insert(insert_idx, placeholder)
            else:
                placeholder = {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": "unknown",
                    "content": "已恢复执行"
                }
                agent.messages.append(placeholder)

    if (orphan_tools or missing or has_reordered) and agent.session:
        await agent.session.replace_all(agent.messages)


async def apply_sliding_window_and_scratchpad(agent) -> None:
    """滑动窗口截断 + 首发意图锁定 + 工具摘要防蒸发。
    【已废弃/禁用】：为了严格捍卫 DeepSeek 的 100% 缓存命中前缀，此功能被永久禁用。
    对话超长由疲劳机制（Fatigue）熔断。
    """
    return

    if len(agent.messages) <= 50:
        return

    # 找安全切分点
    split_idx = len(agent.messages) - 40
    safe_split = -1
    while split_idx < len(agent.messages):
        msg = agent.messages[split_idx]
        if msg.get("role") == "user":
            prev_is_incomplete = False
            if split_idx > 0:
                prev_msg = agent.messages[split_idx - 1]
                if prev_msg.get("role") == "assistant" and prev_msg.get("tool_calls"):
                    prev_is_incomplete = True
            if not prev_is_incomplete:
                safe_split = split_idx
                break
        split_idx += 1

    if safe_split == -1:
        return

    # 从被丢弃的消息中提取工具结果摘要
    tool_snippets = []
    for m in agent.messages[:safe_split]:
        if m.get("role") == "tool" and m.get("content"):
            name = m.get("name", "?")
            text = str(m.get("content", ""))
            if len(text) > 20 and not any(ind in text[:30] for ind in ERROR_INDICATORS):
                tool_snippets.append(f"[{name}] {text[:120].strip()}")

    # 首个 system 消息去旧留新
    sys_msgs = [m for m in agent.messages if m.get("role") == "system"]
    primary = sys_msgs[0] if sys_msgs else {"role": "system", "content": ""}
    base = primary["content"]
    for marker in ("\n\n## 原始目标\n", "\n\n## 工具速查\n"):
        idx = base.find(marker)
        if idx >= 0:
            base = base[:idx]

    # 拼入 goal + scratchpad 到 system 正文
    additions = []
    if agent._original_goal:
        additions.append(f"## 原始目标\n{agent._original_goal['content'][:300]}")
    if tool_snippets:
        additions.append("## 工具速查\n" + "\n".join(tool_snippets[-8:]))
    if additions:
        base = base.rstrip() + "\n\n" + "\n\n".join(additions)

    merged_sys = {"role": "system", "content": base}
    recent_msgs = [m for m in agent.messages[safe_split:]
                   if m.get("role") != "system"]
    agent.messages = [merged_sys] + recent_msgs

    if agent.session:
        await agent.session.replace_all(agent.messages)


def repair_truncated_json(input_str: str) -> str:
    """截断参数 JSON 自动补全闭合器 (Python 极其精纯版)"""
    if not input_str or not input_str.strip():
        return "{}"
    
    # 快速通道：本身就是合法的 JSON
    import json
    try:
        json.loads(input_str)
        return input_str
    except ValueError:
        pass

    stack = []
    escaped = False
    in_string = False
    last_significant = -1

    for i, c in enumerate(input_str):
        if not c.isspace():
            last_significant = i
        if escaped:
            escaped = False
            continue
        if in_string:
            if c == "\\":
                escaped = True
                continue
            if c == '"':
                in_string = False
                stack.pop()
            continue
        if c == '"':
            in_string = True
            stack.append('"')
            continue
        if c in ("{", "["):
            stack.append(c)
        elif c in ("}", "]"):
            if stack:
                stack.pop()

    s = input_str[:last_significant + 1]

    # 1. 剔除截断处的末尾多余逗号 ,
    if s.endswith(","):
        s = s[:-1]

    # 2. 如果截断在 key 后的冒号，自动补齐 null
    import re
    if re.search(r'"\s*:\s*$', s):
        s += " null"

    # 3. 如果在字符串内部截断，补齐双引号
    if in_string:
        s += '"'
        if stack and stack[-1] == '"':
            stack.pop()

    # 4. 按照嵌套栈的逆序闭合括号
    while stack:
        top = stack.pop()
        if top == "{":
            s += "}"
        elif top == "[":
            s += "]"
        elif top == '"':
            s += '"'

    try:
        json.loads(s)
        return s
    except Exception:
        # 极端损坏退路：安全返回 {} 以在工具校验中优雅报错引导大模型重试
        return "{}"


def scavenge_tool_calls(text: str, allowed_names: list) -> list:
    """从大模型思维溢出的 reasoning 或正文中抢救性提取 JSON 工具调用 (限定处于 Markdown 代码块中)"""
    if not text:
        return []

    # 预检：没有疑似 tool call 特征则直接跳过
    if not ("```" in text and ('"name"' in text or '"function"' in text or '<DSML' in text)):
        return []

    import re
    import json
    allowed = set(allowed_names)
    out = []
    
    # 物理安全：只提取被 ```json ... ``` 或 ``` ... ``` 代码块包裹的文本段落
    blocks = []
    # 匹配 ```json ... ```
    for m in re.finditer(r'```json\s*([\s\S]*?)\s*```', text):
        blocks.append(m.group(1))
    # 匹配 ``` ... ```
    for m in re.finditer(r'```\s*(\{[\s\S]*?\})\s*```', text):
        blocks.append(m.group(1))
    
    # 支持 XML 标签变体：<DSML|invoke name="...">...</DSML|invoke>
    for m in re.finditer(r'<[｜|]DSML[｜|]invoke\s+name="([^"]+)">([\s\S]*?)<\/[｜|]DSML[｜|]invoke>', text):
        name = m.group(1)
        body = m.group(2)
        if name in allowed:
            # 提取 parameter 参数
            args = {}
            for pm in re.finditer(r'<[｜|]DSML[｜|]parameter\s+name="([^"]+)"(?:\s+string="(true|false)")?\s*>([\s\S]*?)<\/[｜|]DSML[｜|]parameter>', body):
                key = pm.group(1)
                val = pm.group(3).strip()
                args[key] = val
            out.append({
                "id": f"call_scavenged_dsml_{name}_{m.start()}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args)
                }
            })

    for block in blocks:
        block_clean = block.strip()
        if not block_clean:
            continue
        
        # 提取 block 内最外层的 { ... } JSON 结构
        n = len(block_clean)
        for i in range(n):
            if block_clean[i] != "{":
                continue
            depth = 0
            in_string = False
            escaped = False
            for j in range(i, n):
                c = block_clean[j]
                if escaped:
                    escaped = False
                    continue
                if in_string:
                    if c == "\\":
                        escaped = True
                        continue
                    if c == '"':
                        in_string = False
                    continue
                if c == '"':
                    in_string = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = block_clean[i:j + 1]
                        try:
                            # 尝试对候选 block 内 JSON 进行闭合自愈
                            repaired = repair_truncated_json(candidate)
                            parsed = json.loads(repaired)
                            
                            # 校验 3 种变体
                            # 变体 1: { "name": "...", "arguments": "..." }
                            if isinstance(parsed, dict) and isinstance(parsed.get("name"), str) and parsed["name"] in allowed:
                                arguments = parsed.get("arguments", "{}")
                                out.append({
                                    "id": f"call_scavenged_name_{parsed['name']}_{i}",
                                    "type": "function",
                                    "function": {
                                        "name": parsed["name"],
                                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)
                                    }
                                })
                            # 变体 2: { "type": "function", "function": { "name": "...", "arguments": "..." } }
                            elif isinstance(parsed, dict) and parsed.get("type") == "function" and isinstance(parsed.get("function"), dict):
                                fn = parsed["function"]
                                if isinstance(fn.get("name"), str) and fn["name"] in allowed:
                                    arguments = fn.get("arguments", "{}")
                                    out.append({
                                        "id": f"call_scavenged_fn_{fn['name']}_{i}",
                                        "type": "function",
                                        "function": {
                                            "name": fn["name"],
                                            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)
                                        }
                                    })
                            # 变体 3: { "tool_name": "...", "tool_args": "..." }
                            elif isinstance(parsed, dict) and isinstance(parsed.get("tool_name"), str) and parsed["tool_name"] in allowed:
                                arguments = parsed.get("tool_args", "{}")
                                out.append({
                                    "id": f"call_scavenged_tool_{parsed['tool_name']}_{i}",
                                    "type": "function",
                                    "function": {
                                        "name": parsed["tool_name"],
                                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)
                                    }
                                })
                        except Exception:
                            pass
                        i = j
                        break
            if len(out) >= 8:  # 限制单次提取上限
                break
                
    # ── [极具洞察力的尾部最新优先排重] ──
    unique_calls = {}
    for tc in out:
        name = tc["function"]["name"]
        unique_calls[name] = tc
        
    return list(unique_calls.values())
