import re
import json
import math
import hashlib
import asyncio
import logging
import operator
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("evolution.dream")

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
    
    # 2. 算 cosine similarity，查重（物理消除乘法星号）
    q_mag = math.sqrt(sum(operator.mul(x, x) for x in new_embedding))
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
                    k_mag = math.sqrt(sum(operator.mul(x, x) for x in k_vec))
                    if k_mag > 0:
                        dot = sum(operator.mul(a, b) for a, b in zip(new_embedding, k_vec))
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

async def trigger_deep_dream_evolution(agent):
    """深度长眠做梦进化主梦境协程：提炼全局 KI 并吞噬去重，检测 SOP 并突变合成新 Skill"""
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
                    from ..skills import register_skill_evolution
                    script_name = result_skill.get("helper_script_filename")
                    script_code = result_skill.get("helper_script_content")
                    register_skill_evolution(folder_name, md_content, script_name, script_code, agent=agent)
                    logger.info(f"🎉 [技能进化成功] 自进化突变合成全新技能: 【{skill_name}】 -> skills/{folder_name}/")
    except Exception as e:
        logger.error(f"❌ [技能突变异常] 自进化合成 Skill 失败: {e}")
