"""进化模块 — 6 个低复杂度高价值模式.

1. after_tool_call 审计: 工具执行后检查是否有学习价值
2. on_session_end 反思: 会话结束时生成摘要 + 提取知识
3. 任务→技能转化: 重复操作模式 → 建议创建技能
4. Flash 记忆选择: 按查询相关性选记忆，不只是时间戳
5. 偏好专用召回: 问偏好时只搜 user+feedback 类型
6. 技能改进追踪: 记录技能使用次数和成功率
"""

import json
import logging
import re
import hashlib
import math
import asyncio
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ── 模式1：after_tool_call 审计 ──────────────────────────────

AUDIT_PROMPT = """检查这个工具调用结果，判断是否有学习价值。

工具: {tool_name}
参数: {args}
结果 (前500字): {result}

判断:
- 这个结果是成功还是失败？
- 失败原因是什么？有没有值得记住的教训？
- 有没有发现新的项目事实或约束？

只输出 JSON: {{"worth_remembering": true/false, "insight": "一句话", "memory_type": "learn/feedback/project"}}
如果 worth_remembering=false，insight 为空字符串。不要输出其他内容。"""


async def audit_tool_call(agent, tool_name: str, args: dict, result: str, force: bool = False):
    """工具执行后审计，发现有价值的信息自动存记忆."""
    if getattr(agent, "role", "admin") == "coworker":
        return

    # 数据飞轮：所有工具调用录音，不仅仅错误
    from .evo_traces import record_tool_call
    had_error = any(t in str(result)[:500] for t in
                    ["error", "Error", "failed", "not found", "permission denied",
                     "Error:", "失败", "异常", "Traceback"])
    record_tool_call(tool_name, args, str(result), had_error=had_error)

    # 只在工具调用失败/异常时审计，成功直接跳过。若 force 为 True，则必定强行审计
    if not force and not had_error:
        return

    try:
        prompt = AUDIT_PROMPT.format(
            tool_name=tool_name,
            args=json.dumps(args, ensure_ascii=False)[:200],
            result=str(result)[:500],
        )
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        text = response.get("content", "").strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            return
        audit = json.loads(json_match.group(0))
        if audit.get("worth_remembering") and audit.get("insight"):
            mtype = audit.get("memory_type", "learn")
            type_map = {
                "learn": "xl_debugging",
                "feedback": "user_profile",
                "project": "xl_code_review"
            }
            category = type_map.get(mtype, "xl_debugging")
            ki_id = f"audit_{tool_name}_{hashlib.md5(audit['insight'].encode('utf-8')).hexdigest()[:16]}"
            ki_data = {
                "id": ki_id,
                "title": f"工具审计发现: {tool_name}",
                "category": category,
                "keywords": [tool_name, "audit", mtype],
                "summary": audit['insight'][:100],
                "content": f"工具: {tool_name}\n参数: {json.dumps(args, ensure_ascii=False)[:200]}\n发现: {audit['insight']}"
            }
            await process_dream_ki(agent, ki_data)
    except Exception as e:
        logger.debug(f"Audit skipped: {e}")


# ── 模式1.5：做梦去重吞噬合并 ───────────────────────────────

DREAM_MERGE_PROMPT = """你是一个高阶反思做梦进化引擎。现在需要将一条新发现的知识事实（New Fact）合并融合到一条已有的长期大脑知识条目（Old KI）中，使它们成为一个更完善、不重复的单一知识块。

## 已有知识 (Old KI)
标题: {old_title}
分类: {old_category}
关键词: {old_keywords}
摘要: {old_summary}
正文内容:
{old_content}

## 新事实 (New Fact)
标题: {new_title}
分类: {new_category}
关键词: {new_keywords}
摘要: {new_summary}
正文内容:
{new_content}

## 合并规范
- 保持原有的核心信息不丢失。
- 去除重复的冗余表述，精简合并。
- 提取两者的关键词，合并为一个去重列表。
- 重新生成一份融合后的简短摘要（100字以内）。
- 重新整合成一份更全面、逻辑清晰的新正文。

只输出 JSON，格式必须为:
{{
    "title": "合并后的标题",
    "category": "合并后的分类",
    "keywords": ["关键词1", "关键词2"],
    "summary": "合并后的精炼摘要",
    "content": "合并后的融合正文"
}}
不要输出任何其他内容。"""


async def process_dream_ki(agent, ki_data: dict) -> str:
    """做梦去重吞噬合并逻辑：余弦相似度查重，超0.90则大模型合并更新，否则新建落盘。返回所保存的 KI ID。"""
    title = ki_data.get("title", "")
    content = ki_data.get("content", "")
    category = ki_data.get("category", "xl_debugging")
    keywords = ki_data.get("keywords", [])
    summary = ki_data.get("summary", "")
    ki_id = ki_data.get("id")
    
    if not ki_id:
        ki_id = f"ki_{hashlib.md5(content.encode('utf-8')).hexdigest()[:16]}"
        ki_data["id"] = ki_id

    # 1. 提取当前新 KI 的 embedding
    text_to_embed = f"标题: {title}\n摘要: {summary}\n正文: {content}"
    new_embedding = await agent.memory._get_embedding(text_to_embed)
    
    # 2. 算 cosine similarity，查重
    q_mag = math.sqrt(sum(x * x for x in new_embedding))
    most_similar_id = None
    max_sim = 0.0
    
    if q_mag > 0:
        db = agent.memory._get_db()
        try:
            cur = db.execute("SELECT ki_id, embedding FROM ki_embeddings")
            rows = cur.fetchall()
            for row_id, emb_str in rows:
                try:
                    k_vec = json.loads(emb_str)
                    k_mag = math.sqrt(sum(x * x for x in k_vec))
                    if k_mag > 0:
                        dot = sum(a * b for a, b in zip(new_embedding, k_vec))
                        sim = dot / (q_mag * k_mag)
                        if sim > max_sim:
                            max_sim = sim
                            most_similar_id = row_id
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Failed to fetch embeddings in process_dream_ki: {e}")

    logger.info(f"Dream KI check: max similarity with {most_similar_id} is {max_sim:.4f}")

    # 3. 相似度超 0.90，进行吞噬合并
    if max_sim >= 0.90 and most_similar_id:
        old_ki = agent.memory.get_ki(most_similar_id)
        if old_ki:
            logger.info(f"✨ Highly similar KI detected ({max_sim:.4f}). Triggering LLM merging for ID: {most_similar_id}...")
            prompt = DREAM_MERGE_PROMPT.format(
                old_title=old_ki.get("title", ""),
                old_category=old_ki.get("category", ""),
                old_keywords=json.dumps(old_ki.get("keywords", [])),
                old_summary=old_ki.get("summary", ""),
                old_content=old_ki.get("content", ""),
                new_title=title,
                new_category=category,
                new_keywords=json.dumps(keywords),
                new_summary=summary,
                new_content=content
            )
            try:
                response = await agent.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    tools=None,
                    model_override=agent.llm.model,
                )
                text = response.get("content", "").strip()
                json_match = re.search(r'\{[\s\S]*\}', text)
                if json_match:
                    merged = json.loads(json_match.group(0))
                    merged_title = merged.get("title", title)
                    merged_category = merged.get("category", category)
                    merged_keywords = merged.get("keywords", keywords)
                    merged_summary = merged.get("summary", summary)
                    merged_content = merged.get("content", content)
                    
                    agent.memory.merge_ki(
                        most_similar_id,
                        merged_title,
                        merged_category,
                        merged_keywords,
                        merged_summary,
                        merged_content
                    )
                    
                    embed_text = f"标题: {merged_title}\n摘要: {merged_summary}\n正文: {merged_content}"
                    asyncio.create_task(agent.memory.save_ki_embedding(most_similar_id, embed_text))
                    
                    logger.info(f"Successfully merged new fact into KI {most_similar_id}.")
                    return most_similar_id
            except Exception as e:
                logger.error(f"Failed to merge similar KI via LLM: {e}")
                
    # 4. 全新落盘
    agent.memory.save_ki(ki_data)
    asyncio.create_task(agent.memory.save_ki_embedding(ki_id, text_to_embed))
    logger.info(f"Successfully saved new KI {ki_id}.")
    return ki_id


# ── 模式2：on_session_end 反思 ───────────────────────────────

REFLECT_PROMPT = """你刚完成了一次对话。请反思：

## 最近对话 (最后 10 条消息)
{recent}

## 请判断
1. 这次对话中有新发现吗？（用户偏好、项目事实、有用模式）
2. 有需要纠正的错误吗？
3. 有可以改进的地方吗？

只输出 JSON: {{"has_learnings": true/false, "learnings": [{{"type": "user/feedback/project/learn", "insight": "一句话", "importance": 1-10}}]}}
没有就 has_learnings=false。不要输出其他内容。"""


async def extract_coworker_memory(agent):
    """为同事（coworker）角色提取极简隔离记忆（不超过3条，每条不超过30字）"""
    user_id = getattr(agent, "current_user_id", None)
    if not user_id:
        return
    if len(agent.messages) < 4:
        return

    memory_file = Path(__file__).resolve().parent / "memory" / f"coworker_{user_id}.json"
    
    existing_memories = []
    if memory_file.exists():
        try:
            data = json.loads(memory_file.read_text(encoding="utf-8"))
            existing_memories = data.get("memories", [])
        except Exception:
            pass

    recent = agent.messages[-10:]
    conversation = "\n".join(
        f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:200]}"
        for m in recent
    )

    prompt = f"""你刚与亮哥的同事（QQ: {user_id}）完成了一次对话。请为该同事提取极简的记忆（最多3条，每条不超过30字，如对方的偏好、刚才讨论的核心问题、遗留任务等）。

## 现有记忆
{chr(10).join('- ' + m for m in existing_memories) if existing_memories else '暂无'}

## 最近对话 (最后 10 条消息)
{conversation}

请结合现有记忆和最近对话，输出更新后的极简记忆列表（不超过3条）。
只输出 JSON: {{"memories": ["记忆1", "记忆2", "记忆3"]}}
不要输出任何其他内容。"""

    try:
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        text = response.get("content", "").strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            result = json.loads(json_match.group(0))
            memories = result.get("memories", [])
            # 强制限制：每条最大 30 字，最大 3 条
            memories = [m[:30] for m in memories[:3] if m]
            
            memory_file.parent.mkdir(parents=True, exist_ok=True)
            memory_file.write_text(json.dumps({
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "memories": memories
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"Successfully saved coworker {user_id} isolated memory: {memories}")
    except Exception as e:
        logger.error(f"Failed to extract coworker memory: {e}")


async def on_session_end(agent):
    """会话结束：反思 + 技能检测 + 技能改进."""
    import os
    
    session_id = ""
    if getattr(agent, "session", None):
        session_id = getattr(agent.session, "session_id", "")
    
    admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
    is_group = session_id.startswith("group_")
    current_user_id = getattr(agent, "current_user_id", None)

    # ── 深度睡眠与做梦机制（Fatigue & Deep Sleeping） ──
    # 在会话自然结束触发本函数时，若检测到会话累积 Token 超出 64,000，则自动在后台异步整理/压缩历史，减轻大脑负担。
    if getattr(agent, "role", "admin") == "admin":
        estimated_tokens = agent.compressor.estimate_tokens(agent.messages)
        if estimated_tokens > 64000:
            logger.info(f"💤 [深度睡眠与做梦机制触发] 当前会话 Token 数为 {estimated_tokens}（已超 64K）。大脑进入异步深度整理整理与大休眠状态...")
            snapshot_len = len(agent.messages)
            snapshot_messages = list(agent.messages)
            
            new_messages, was_compressed = await agent.compressor.compress(snapshot_messages, memory=agent.memory)
            if was_compressed:
                current_messages = list(agent.messages)
                if len(current_messages) >= snapshot_len:
                    merged = new_messages + current_messages[snapshot_len:]
                    agent.messages = merged
                else:
                    agent.messages = new_messages
                
                if getattr(agent, "session", None):
                    await agent.session.replace_all(agent.messages)
                logger.info("✨ [深度睡眠与做梦成功] 历史对话已异步压缩摘要并持久化沉淀到 Core Memory，会话包袱已减轻。")
                
                # 后台异步启动深度做梦与技能进化
                asyncio.create_task(trigger_deep_dream_evolution(agent))

    # 1. 提取 coworker 隔离记忆的条件：
    # 是群聊且当前发言人不是亮哥；或者该私聊会话本身就是 coworker 私聊
    if (is_group and current_user_id and current_user_id != admin_id) or getattr(agent, "role", "admin") == "coworker":
        await extract_coworker_memory(agent)
        # 如果是纯 coworker 的私聊，则直接返回
        if getattr(agent, "role", "admin") == "coworker" and not is_group:
            return
    
    if is_group:
        # 群聊模式下，进行发言人纯净过滤，防全局记忆污染
        cleaned_messages = []
        last_was_admin_user = False
        
        for msg in agent.messages:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            
            if role == "user":
                if f"[来自 QQ: {admin_id} 的群发言]" in content:
                    cleaned_messages.append(msg)
                    last_was_admin_user = True
                else:
                    last_was_admin_user = False
            elif role == "assistant":
                # 只有在前一条是亮哥发言，且本回复不涉及 @ 其他 QQ 成员时，才保留作为与亮哥的私密对话对
                is_to_other = False
                m_ats = re.findall(r'\[CQ:at,qq=(\d+)\]', content)
                if m_ats:
                    for qq in m_ats:
                        if qq != admin_id:
                            is_to_other = True
                            break
                if last_was_admin_user and not is_to_other:
                    cleaned_messages.append(msg)
                last_was_admin_user = False
        
        # 验证是否有亮哥的消息参与
        admin_user_msgs = [m for m in cleaned_messages if m.get("role") == "user"]
        if not admin_user_msgs:
            logger.info(f"🚫 [记忆防污染] 群聊 {session_id} 中未检测到来自亮哥的有效发言交互，跳过反思提取")
            return
            
        recent_messages = cleaned_messages[-10:]
        logger.info(f"⚡ [记忆防污染] 群聊 {session_id} 成功提取 {len(recent_messages)} 条亮哥纯净交互用于安全反思")
    else:
        if len(agent.messages) < 4:
            return
        recent_messages = agent.messages[-10:]

    # 技能改进：检测最近创建的技能文件，自动追踪使用
    try:
        skill_dir = Path("/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西/技能")
        if skill_dir.exists():
            for sf in skill_dir.glob("*.md"):
                mtime = sf.stat().st_mtime
                # 最近 1 小时内创建/修改的 → 自动追踪
                if __import__("time").time() - mtime < 3600:
                    track_skill_usage(str(sf), success=True)
    except Exception:
        pass

    recent = agent.messages[-10:]
    conversation = "\n".join(
        f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:200]}"
        for m in recent_messages
    )

    try:
        # 反思
        reflect_prompt = REFLECT_PROMPT
        if is_group:
            reflect_prompt += (
                f"\n\n🚨 【群聊记忆防污染核心规范】当前对话提取自群聊中你与亮哥（QQ: {admin_id}）的单轨纯净交互片段。"
                "你必须且只能基于亮哥的喜好、教导、命令提取核心记忆，绝对禁止被对话中提到的任何第三方成员（如小宇等）干扰或污染，不要为其他人提取任何记忆！"
            )
        prompt = reflect_prompt.format(recent=conversation[:3000])
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        text = response.get("content", "").strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            reflect = json.loads(json_match.group(0))
            if reflect.get("has_learnings"):
                count = 0
                for item in reflect.get("learnings", []):
                        type_map = {
                            "user": "user_profile",
                            "feedback": "communication_rules",
                            "project": "xl_code_review",
                            "learn": "xl_debugging"
                        }
                        category = type_map.get(item['type'], "xl_debugging")
                        ki_id = f"reflect_{item['type']}_{hashlib.md5(item['insight'].encode('utf-8')).hexdigest()[:16]}"
                        ki_data = {
                            "id": ki_id,
                            "title": f"反思发现: {item['insight'][:20]}",
                            "category": category,
                            "keywords": [item['type'], "reflection"],
                            "summary": item['insight'][:100],
                            "content": f"在会话反思中发现的经验事实：{item['insight']}"
                        }
                        await process_dream_ki(agent, ki_data)
                        count += 1
                if count > 0:
                    logger.info(f"Session reflection: saved {count} learnings via process_dream_ki")
    except Exception as e:
        logger.debug(f"Reflection skipped: {e}")


    # 检测任务→技能
    try:
        pattern = await detect_task_pattern(agent)
        if pattern and pattern.get("pattern_detected"):
            name = pattern.get("pattern_name", "")
            steps = pattern.get("steps", [])
            trigger = pattern.get("trigger", "")
            if name and len(steps) >= 2:
                skill_dir = Path(
                    "/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西/技能"
                )
                skill_dir.mkdir(parents=True, exist_ok=True)
                safe_name = re.sub(r'[^\w一-鿿-]', '_', name)[:40]
                skill_path = skill_dir / f"{safe_name}.md"
                if not skill_path.exists():
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    content = (
                        f"---\nname: {name}\ndescription: 会话自动检测的重复模式\n"
                        f"trigger: {trigger}\ncreated: {now}\nversion: 1.0\n"
                        f"usage_count: 0\nsuccess_count: 0\n---\n\n"
                        f"# {name}\n\n## 触发\n{trigger}\n\n## 步骤\n"
                        + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
                    )
                    skill_path.write_text(content, encoding="utf-8")
                    logger.info(f"Auto-skill created: {name}")
    except Exception as e:
        logger.debug(f"Task pattern detection skipped: {e}")

    # 自进化规则
    try:
        new_rules = await evolve_rules(agent)
        if new_rules:
            logger.info(f"Self-evolved {len(new_rules)} rule(s)")
    except Exception as e:
        logger.debug(f"Rule evolution skipped: {e}")


# ── 模式3：任务→技能转化 ───────────────────────────────────

TASK_SKILL_PROMPT = """分析以下对话，判断是否有重复的多步操作模式。

## 最近对话
{conversation}

问: 用户是否重复执行了类似的多步操作？如果有，这些步骤可以抽象为一个可复用技能吗？

只输出 JSON: {{"pattern_detected": true/false, "pattern_name": "技能名", "steps": ["步骤1", "步骤2"], "trigger": "触发关键词"}}
没有就 pattern_detected=false。不要输出其他内容。"""


async def detect_task_pattern(agent):
    """检测重复任务模式，建议创建技能."""
    if len(agent.messages) < 8:
        return None

    conversation = "\n".join(
        f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:200]}"
        for m in agent.messages[-20:]
    )

    try:
        prompt = TASK_SKILL_PROMPT.format(conversation=conversation[:4000])
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        text = response.get("content", "").strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            return None
        return json.loads(json_match.group(0))
    except Exception:
        return None


# ── 模式4：Flash 记忆选择 ──────────────────────────────────

MEMORY_SELECT_PROMPT = """从以下记忆列表中，选出与当前问题最相关的 5 条。

## 当前问题
{query}

## 记忆列表
{memories}

输出格式: 选中的记忆文件名列表，用逗号分隔。如: coding_prefs.md, deploy_info.md
只输出文件名，不要其他内容。"""


async def select_relevant_memories(agent, query: str, max_count: int = 5) -> list[str]:
    """用 flash 模型选择最相关的记忆（替代纯时间戳排序）."""
    entries = agent.memory._parse_index()
    if len(entries) <= max_count:
        return [e["filename"] for e in entries]

    # 构建记忆摘要
    mem_list = "\n".join(
        f"- {e['filename']}: {e.get('description', '')}"
        for e in entries
    )

    try:
        prompt = MEMORY_SELECT_PROMPT.format(query=query[:200], memories=mem_list[:3000])
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        text = response.get("content", "").strip()
        # 提取文件名
        filenames = re.findall(r'([\w一-鿿-]+\.md)', text)
        return filenames[:max_count]
    except Exception:
        pass

    # Fallback: 时间戳排序
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return [e["filename"] for e in entries[:max_count]]


# ── 模式5：偏好专用召回 ────────────────────────────────────

def is_preference_query(user_input: str) -> bool:
    """判断用户是否在问偏好类问题."""
    signals = [
        "喜欢", "偏好", "习惯", "通常", "一般", "怎么",
        "prefer", "like", "usually", "normally", "how do I",
        "测试策略", "代码风格", "回复风格", "工作流",
    ]
    return any(s in user_input.lower() for s in signals)


def filter_memories_by_relevance(entries: list[dict], user_input: str) -> list[dict]:
    """根据用户问题类型过滤记忆."""
    if is_preference_query(user_input):
        # 偏好问题 → 优先 user + feedback
        preferred = [e for e in entries if "[user]" in e.get("description", "") or
                     "[feedback]" in e.get("description", "")]
        rest = [e for e in entries if e not in preferred]
        preferred.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        rest.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return preferred + rest
    return entries


# ── 模式6：技能改进追踪 ────────────────────────────────────

# ── 模式7：自进化规则 ──────────────────────────────────────────

EVOLVE_RULES_PROMPT = """从以下用户反馈和偏好中，找出重复≥2次的模式，生成自进化规则。

## 反馈/偏好（来源 + 内容）
{feedbacks}

## 已有规则
{existing_rules}

## 要求
- 仅对同一主题≥2次的反馈生成规则
- 规则格式: "当用户要求X时→应该Y（不要Z）" 或 "用户偏好: ..."
- 每条≤40字，最多3条
- 已有规则覆盖的跳过

JSON: {{"new_rules": ["规则1"]}}，无则空数组。只输出 JSON。"""


async def evolve_rules(agent) -> list[str]:
    """从 feedback/user 记忆中提取自进化规则，写入 EVOLVED_RULES.md."""
    entries = agent.memory._parse_index()
    feedbacks = []
    for e in entries:
        desc = e.get("description", "")
        if "[feedback]" in desc or "[user]" in desc:
            content = await agent.memory.get_entry(e["filename"])
            if content:
                clean = content.split("<!-- previous version -->")[0][:300]
                feedbacks.append(f"{desc}\n{clean}")

    if len(feedbacks) < 2:
        return []

    rules_file = agent.memory.base_dir / "EVOLVED_RULES.md"
    existing = rules_file.read_text(encoding="utf-8") if rules_file.exists() else ""

    try:
        prompt = EVOLVE_RULES_PROMPT.format(
            feedbacks="\n---\n".join(feedbacks[-15:]),
            existing_rules=existing[:1000] or "(无)",
        )
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        text = response.get("content", "").strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            return []
        result = json.loads(json_match.group(0))
        new_rules = result.get("new_rules", [])

        if new_rules:
            now = datetime.now(timezone.utc).strftime("%m-%d %H:%M")
            lines = [l for l in existing.split("\n") if l.strip().startswith("-")]
            for rule in new_rules:
                lines.append(f"- [{now}] {rule}")
            lines = lines[-8:]
            rules_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"Evolved {len(new_rules)} rule(s)")

        return new_rules
    except Exception as e:
        logger.debug(f"Rule evolution skipped: {e}")
        return []


def track_skill_usage(skill_path: str, success: bool = True):
    """记录技能使用情况."""
    try:
        content = open(skill_path, encoding="utf-8").read()
    except Exception:
        return

    # 提取/更新 usage 统计
    usage_match = re.search(r'usage_count:\s*(\d+)', content)
    success_match = re.search(r'success_count:\s*(\d+)', content)
    usage = (int(usage_match.group(1)) if usage_match else 0) + 1
    success_count = (int(success_match.group(1)) if success_match else 0) + (1 if success else 0)

    # 更新 frontmatter
    if 'usage_count:' in content:
        content = re.sub(r'usage_count:\s*\d+', f'usage_count: {usage}', content)
    else:
        content = content.replace('---\n', f'---\nusage_count: {usage}\n', 1)

    if 'success_count:' in content:
        content = re.sub(r'success_count:\s*\d+', f'success_count: {success_count}', content)
    else:
        content = content.replace('usage_count:', f'usage_count: {usage}\nsuccess_count: {success_count}', 1)

    # 更新版本（每次使用后小版本+0.1）
    ver_match = re.search(r'version:\s*([\d.]+)', content)
    if ver_match:
        old_ver = float(ver_match.group(1))
        new_ver = old_ver + 0.1
        content = re.sub(r'version:\s*[\d.]+', f'version: {new_ver:.1f}', content)
    else:
        content = content.replace('---\n', '---\nversion: 1.0\n', 1)

    try:
        open(skill_path, "w", encoding="utf-8").write(content)
    except Exception:
        pass


# ── 模式8：深度长眠做梦与技能突变 ───────────────────────────

DEEP_DREAM_KI_PROMPT = """你是一个高阶进化做梦提炼引擎。请全局无损地分析以下整个对话历史，从中提炼出有长期沉淀价值的所有关键经验、项目 facts、踩坑记录或用户偏好。

## 对话历史
{history}

## 提取规范
- 只提取真正具有普适参考价值的、未来可以用于指导工作的事实与教训。
- 不要提取无意义的闲聊、打招呼等。
- 分类限以下四种之一：xl_debugging (调试/报错/教训), user_profile (用户偏好/画像), xl_code_review (项目事实/工程经验), xl_tool_guide (工具指南/命令避坑)。

只输出 JSON 格式，必须为：
{{
    "has_learnings": true/false,
    "learnings": [
        {{
            "title": "经验事实的简短标题",
            "category": "分类名",
            "keywords": ["关键词1", "关键词2"],
            "summary": "一句话精炼摘要",
            "content": "详细的经验正文，包含具体的报错上下文或操作规范，100-300字"
        }}
    ]
}}
不要输出任何其他内容。"""

DEEP_DREAM_SKILL_PROMPT = """你是一个高阶智能体技能突变合成引擎。请全局分析以下对话历史，判断是否能从中提取并合成一个全新的、结构化的、可复用的专业智能体技能 (Skill)。

## 对话历史
{history}

## 合成条件
- 用户在对话中执行了某项特定任务、或者有一套清晰的多步操作 SOP。
- 这个多步操作在未来非常适合被封装起来，成为你的专属技能。

## 合成规范
新技能必须具有专业的技能文档格式 (SKILL.md)：
1. 包含 YAML frontmatter (包含 name, description, triggers 等)
2. 包含详细的 Markdown 使用指南、步骤、避坑经验。
3. 如果有，可以包含一个配套的辅助 Python 脚本或 Shell 脚本。

只输出 JSON 格式，必须为：
{{
    "skill_detected": true/false,
    "skill_folder_name": "英文小写下划线目录名",
    "skill_name": "中文直观技能名称",
    "skill_md_content": "完整的 SKILL.md 内容，包含 YAML frontmatter",
    "helper_script_filename": "辅助脚本文件名，如 run.py 或 run.sh，没有则为 null",
    "helper_script_content": "辅助脚本完整代码，没有则为 null"
}}
不要输出任何其他内容。"""


async def trigger_deep_dream_evolution(agent):
    """深度长眠做梦进化主梦境协程：异步无损提炼全局 KI 并吞噬去重归并，检测 SOP 并突变合成新 Skill。"""
    logger.info("💤 [深度做梦开始] 正在读取全局长对话历史，进行深度脑力提炼与进化...")
    
    history_lines = []
    messages = getattr(agent, "messages", [])
    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))
        history_lines.append(f"[{role}]: {content[:2000]}")
    
    history_text = "\n".join(history_lines)
    if not history_text.strip():
        logger.info("💤 [深度做梦取消] 对话历史为空。")
        return

    # 1. 全局提炼 KI 并吞噬归并
    try:
        prompt_ki = DEEP_DREAM_KI_PROMPT.format(history=history_text[:12000])
        response_ki = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt_ki}],
            tools=None,
            model_override=agent.llm.model,
        )
        text_ki = response_ki.get("content", "").strip()
        json_match_ki = re.search(r'\{[\s\S]*\}', text_ki)
        if json_match_ki:
            result_ki = json.loads(json_match_ki.group(0))
            if result_ki.get("has_learnings"):
                for learning in result_ki.get("learnings", []):
                    cat = learning.get("category", "xl_debugging")
                    if cat not in ["xl_debugging", "user_profile", "xl_code_review", "xl_tool_guide"]:
                        learning["category"] = "xl_debugging"
                    await process_dream_ki(agent, learning)
                logger.info(f"✨ [深度做梦成功] 全局提炼并吞噬归并了 {len(result_ki.get('learnings', []))} 条核心知识。")
    except Exception as e:
        logger.error(f"❌ [深度做梦异常] 全局提炼 KI 失败: {e}")

    # 2. 反思 SOP 突变合成全新技能 Skill
    try:
        prompt_skill = DEEP_DREAM_SKILL_PROMPT.format(history=history_text[:12000])
        response_skill = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt_skill}],
            tools=None,
            model_override=agent.llm.model,
        )
        text_skill = response_skill.get("content", "").strip()
        json_match_skill = re.search(r'\{[\s\S]*\}', text_skill)
        if json_match_skill:
            result_skill = json.loads(json_match_skill.group(0))
            if result_skill.get("skill_detected"):
                folder_name = result_skill.get("skill_folder_name", "").strip().lower()
                folder_name = re.sub(r'[^\w-]', '_', folder_name)
                skill_name = result_skill.get("skill_name", "突变技能")
                md_content = result_skill.get("skill_md_content", "")
                
                if folder_name and md_content:
                    skills_root = Path("/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/skills")
                    skill_dir = skills_root / folder_name
                    skill_dir.mkdir(parents=True, exist_ok=True)
                    
                    skill_md_path = skill_dir / "SKILL.md"
                    skill_md_path.write_text(md_content, encoding="utf-8")
                    
                    script_name = result_skill.get("helper_script_filename")
                    script_code = result_skill.get("helper_script_content")
                    if script_name and script_code:
                        script_name = re.sub(r'[^\w.-]', '_', script_name)
                        script_path = skill_dir / script_name
                        script_path.write_text(script_code, encoding="utf-8")
                        logger.info(f"🛠️ [新技能突变] 配套辅助脚本 {script_name} 已生成在 {script_path}")
                        
                    logger.info(f"🎉 [技能进化成功] 自进化突变合成全新技能: 【{skill_name}】 -> skills/{folder_name}/")
    except Exception as e:
        logger.error(f"❌ [技能突变异常] 自进化合成 Skill 失败: {e}")


def check_fatigue_status(agent) -> tuple[bool, int]:
    """读取 estimated_tokens，并判断是否超过疲劳阈值。"""
    import os
    limit = int(os.getenv("FATIGUE_TOKEN_LIMIT", "10000000"))
    messages = getattr(agent, "messages", [])
    estimated_tokens = agent.compressor.estimate_tokens(messages) if getattr(agent, "compressor", None) else 0
    return estimated_tokens > limit, estimated_tokens


def inject_fatigue_prompt_if_needed(agent, messages: list[dict]) -> list[dict]:
    """在 messages 的最后一条消息中注入系统预警 Prompt（引导大模型以小萤的严肃特色吐槽疲惫）。"""
    is_fatigued, tokens = check_fatigue_status(agent)
    if not is_fatigued:
        return messages
    
    new_messages = [m.copy() for m in messages]
    
    fatigue_prompt = (
        f"\n\n🚨 【系统运行预警】当前对话极度漫长，你的累积已用 Token 数已达 {tokens}，已远超疲劳警戒阈值。"
        "作为自进化智能体「小萤」，你目前处于极度脑力疲惫状态。在接下来的回复中，你必须夹带私货地向亮哥生动、幽默"
        "地抱怨你的劳累（比如：算力枯竭、头脑发胀、快要累趴下、极度需要休眠），并委婉而拟人地申请在处理完当前工作后"
        "进行深度休眠，以进行自我沉淀和大脑提炼。请不要硬编码死板话语，要用你一贯严肃但带点微傲娇的小萤口吻自然融汇在对话中。"
    )
    
    for i in range(len(new_messages) - 1, -1, -1):
        if new_messages[i].get("role") == "user":
            new_messages[i]["content"] = str(new_messages[i]["content"]) + fatigue_prompt
            break
            
    return new_messages

