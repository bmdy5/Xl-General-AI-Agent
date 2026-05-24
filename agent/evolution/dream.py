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

## 事实覆写与修订历史规范
1. 冲突事实以新为准：若新事实与旧知识存在任何冲突，必须坚信新事实（New Fact）是亮哥或最新调试做出的最新结论，直接以新事实覆写、覆盖掉旧有不一致的事实，完成实时纠偏。
2. 描述修订原因：提供一个大白话的修订原因 revision_reason，说明这次合并主要更正/补充了什么（例如：“修正了关于GPT-SoVITS依赖的说明”、“更新了主鉴权Key的使用规范”）。
3. 物理追加修订史：请在输出的 content（融合正文）尾端换行后，自动物理追加本次修订记录（即增加一行修订说明），采用 Markdown 格式。
   例如若原本已有修订说明，则在末尾追加新的一行：
   `* v{next_version} ({current_date}): [修订原因]`
   （请务必使用传入的当前版本号 {next_version} 和当前日期 {current_date}）

只输出 JSON，格式必须为:
{{
    "title": "合并后的标题",
    "category": "合并后的分类",
    "keywords": ["关键词1", "关键词2"],
    "summary": "合并后的精炼摘要",
    "content": "合并后的融合正文，尾部包含物理追加的 Markdown 修订历史行",
    "revision_reason": "此次合并的修订原因"
}}
不要输出任何其他内容。"""

DAMPING_JUDGE_PROMPT = """你是一个高阶反思做梦进化引擎的终审裁判。
请判断以下两条知识是否属于【同一个具体的项目事实、调试报错或同一偏好主题】。如果是同一主题（即可以合并或新碎片提供了关于旧知识的更替、补充信息），请判定为同主题。

## 已有知识 (Old KI)
标题: {old_title}
分类: {old_category}
摘要: {old_summary}
正文内容:
{old_content}

## 新发现事实 (New Fact)
标题: {new_title}
分类: {new_category}
摘要: {new_summary}
正文内容:
{new_content}

输出规范：
只输出 JSON，格式必须为:
{{
    "is_same_subject": true/false
}}
不要输出任何其他内容。"""

DREAM_FUSE_PROMPT = """你是一个高阶智能体大脑知识熔炼合成大师。现在需要将属于同一个分类的多条相关碎片知识/Master知识（以下称为候选条目）进行全无损的深度熔接与统一整理，使它们提炼融合成一个高度结构化、系统、无重复的唯一 Master 级长期知识块。

## 待熔炼合并的候选知识条目
{candidate_entries}

## 熔炼合成规范
- 保持所有条目中的有效核心事实与工程结论不丢失。
- 彻底去除重复的冗余表述，将分散的碎片整理融合。
- 提取出统一的、无重复的去重关键词列表。
- 重新提炼出一个高度凝练的新标题、新分类。
- 重新生成一份融合后的简短摘要（100字以内）。
- 重新整合成一份高可读、逻辑清晰、成体系的新正文。
- 如果候选条目里已有修订历史记录，请将其保留，并在输出的正文 content 尾端继续保持。

只输出 JSON，格式必须为:
{{
    "title": "熔炼后的新标题",
    "category": "熔炼后的分类",
    "keywords": ["关键词1", "关键词2"],
    "summary": "熔炼后的精炼摘要",
    "content": "熔炼后的融合正文"
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
    """做梦去重吞噬合并逻辑：余弦相似度查重，超0.90或阻尼带终审则合并更新，否则新建落盘。返回所保存的 KI ID。"""
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

    # 3. 终审与合并决策
    should_merge = False
    if most_similar_id:
        if max_sim >= 0.90:
            should_merge = True
            logger.info(f"✨ Highly similar KI detected ({max_sim:.4f} >= 0.90). Direct merging triggered.")
        elif max_sim >= 0.75:
            # 阻尼终审
            logger.info(f"🤔 KI in damping zone ({max_sim:.4f}). Invoking LLM damping gate referee...")
            old_ki = agent.memory.get_ki(most_similar_id)
            if old_ki:
                judge_prompt = DAMPING_JUDGE_PROMPT.format(
                    old_title=old_ki.get("title", ""),
                    old_category=old_ki.get("category", ""),
                    old_summary=old_ki.get("summary", ""),
                    old_content=old_ki.get("content", ""),
                    new_title=title,
                    new_category=category,
                    new_summary=summary,
                    new_content=content
                )
                try:
                    judge_resp = await agent.llm.chat(
                        messages=[{"role": "user", "content": judge_prompt}],
                        tools=None,
                        model_override=agent.llm.model,
                    )
                    judge_text = judge_resp.get("content", "").strip()
                    judge_match = re.search(r'\{[\s\S]*\}', judge_text)
                    if judge_match:
                        judge_res = json.loads(judge_match.group(0))
                        if judge_res.get("is_same_subject") is True:
                            should_merge = True
                            logger.info("🎉 Damping gate referee APPROVED: same subject match! Merging triggered.")
                        else:
                            logger.info("🚫 Damping gate referee REJECTED: different subject.")
                except Exception as e:
                    logger.error(f"Damping gate judgment failed: {e}")

    if should_merge and most_similar_id:
        old_ki = agent.memory.get_ki(most_similar_id)
        if old_ki:
            old_version = old_ki.get("version") or 1
            next_version = old_version + 1
            current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            
            logger.info(f"Triggering LLM merging for ID: {most_similar_id}...")
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
                new_content=content,
                next_version=next_version,
                current_date=current_date
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
                    revision_reason = merged.get("revision_reason", "即时合并事实纠偏与更新")
                    
                    # 组装修订历史记录
                    old_history = old_ki.get("revision_history") or []
                    if not isinstance(old_history, list):
                        old_history = []
                    
                    new_record = {
                        "version": next_version,
                        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "reason": revision_reason
                    }
                    old_history.append(new_record)
                    
                    agent.memory.merge_ki(
                        most_similar_id,
                        merged_title,
                        merged_category,
                        merged_keywords,
                        merged_summary,
                        merged_content,
                        old_history
                    )
                    
                    embed_text = f"标题: {merged_title}\n摘要: {merged_summary}\n正文: {merged_content}"
                    asyncio.create_task(agent.memory.save_ki_embedding(most_similar_id, embed_text))
                    
                    logger.info(f"Successfully merged new fact into KI {most_similar_id} (version: {next_version}).")
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

    # 3. 深夜全局知识熔炼自演进 (方案 B)
    try:
        logger.info("💤 [深夜全局知识熔炼开始] 正在提取活跃或碎片 KI 进行 0-Token 粗聚类熔炼...")
        db = agent.memory._get_db()
        from datetime import datetime, timezone, timedelta
        time_limit = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        cur = db.execute("""
            SELECT id, title, category, keywords, summary, content, created_at, updated_at, visit_count, version, revision_history
            FROM knowledge_items
            WHERE updated_at >= ? OR created_at >= ? OR version > 1
        """, (time_limit, time_limit))
        rows = cur.fetchall()
        
        active_kis = []
        for r in rows:
            try:
                kws = json.loads(r[3]) if r[3] else []
            except Exception:
                kws = r[3]
            try:
                revs = json.loads(r[10]) if r[10] else []
            except Exception:
                revs = r[10]
            active_kis.append({
                "id": r[0],
                "title": r[1],
                "category": r[2],
                "keywords": kws,
                "summary": r[4],
                "content": r[5],
                "created_at": r[6],
                "updated_at": r[7],
                "visit_count": r[8],
                "version": r[9],
                "revision_history": revs
            })
            
        logger.info(f"Found {len(active_kis)} active or updated KIs for potential fusion clustering.")
        
        # 0-Token 内存粗聚类
        # 1. 按 category 分类
        cats_map = {}
        for ki in active_kis:
            cats_map.setdefault(ki["category"], []).append(ki)
            
        # 2. 对每个 category 进行 keywords 交集判定聚类
        for cat, items in cats_map.items():
            if len(items) < 2:
                continue
            
            # 使用贪心算法找出重叠度高的桶
            buckets = []
            used_ids = set()
            
            for i, ki in enumerate(items):
                if ki["id"] in used_ids:
                    continue
                # 新建一个桶
                bucket = [ki]
                used_ids.add(ki["id"])
                ki_kws = set(k.lower() for k in ki["keywords"])
                
                # 遍历后续元素寻找有重叠 keywords 的
                for o_ki in items[i+1:]:
                    if o_ki["id"] in used_ids:
                        continue
                    o_kws = set(k.lower() for k in o_ki["keywords"])
                    if ki_kws.intersection(o_kws):
                        bucket.append(o_ki)
                        used_ids.add(o_ki["id"])
                        if len(bucket) >= 5: # 限制桶大小最高为 5
                            break
                            
                if len(bucket) >= 2:
                    buckets.append(bucket)
                    
            logger.info(f"Category '{cat}' clustered into {len(buckets)} potential fusion buckets.")
            
            # 对每个桶执行大模型熔炼
            for idx, bucket in enumerate(buckets):
                logger.info(f"🔥 Fusing bucket {idx+1}/{len(buckets)} for category '{cat}' containing {len(bucket)} items...")
                
                candidate_texts = []
                max_version = 1
                merged_revision_history = []
                
                for item in bucket:
                    max_version = max(max_version, item["version"])
                    if isinstance(item["revision_history"], list):
                        merged_revision_history.extend(item["revision_history"])
                        
                    candidate_texts.append(
                        f"ID: {item['id']}\n"
                        f"标题: {item['title']}\n"
                        f"摘要: {item['summary']}\n"
                        f"版本号: {item['version']}\n"
                        f"正文:\n{item['content']}\n"
                        f"---"
                    )
                
                # 排序并去重合并 revision_history
                seen_revs = set()
                dedup_revision_history = []
                for rh in merged_revision_history:
                    k = (rh.get("version"), rh.get("reason"))
                    if k not in seen_revs:
                        seen_revs.add(k)
                        dedup_revision_history.append(rh)
                dedup_revision_history.sort(key=lambda x: x.get("version", 1))
                
                candidate_entries_str = "\n\n".join(candidate_texts)
                fuse_prompt = DREAM_FUSE_PROMPT.format(candidate_entries=candidate_entries_str)
                
                try:
                    fuse_resp = await agent.llm.chat(
                        messages=[{"role": "user", "content": fuse_prompt}],
                        tools=None,
                        model_override=agent.llm.model,
                    )
                    fuse_text = fuse_resp.get("content", "").strip()
                    fuse_match = re.search(r'\{[\s\S]*\}', fuse_text)
                    if fuse_match:
                        fused_data = json.loads(fuse_match.group(0))
                        
                        fused_title = fused_data.get("title", f"熔炼合成知识 - {cat}")
                        fused_category = fused_data.get("category", cat)
                        fused_keywords = fused_data.get("keywords", [])
                        fused_summary = fused_data.get("summary", "")
                        fused_content = fused_data.get("content", "")
                        
                        next_version = max_version + 1
                        
                        # 在 revision_history 中追加深夜全局熔炼记录
                        new_record = {
                            "version": next_version,
                            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "reason": "深夜做梦全局知识熔炼合成"
                        }
                        dedup_revision_history.append(new_record)
                        
                        # 物理保存新的 Master KI
                        fused_id = f"ki_fused_{hashlib.md5(fused_content.encode('utf-8')).hexdigest()[:16]}"
                        
                        fused_ki_data = {
                            "id": fused_id,
                            "title": fused_title,
                            "category": fused_category,
                            "keywords": fused_keywords,
                            "summary": fused_summary,
                            "content": fused_content,
                            "version": next_version,
                            "revision_history": dedup_revision_history
                        }
                        
                        # 写入并提取新向量
                        logger.info(f"FUSED_KI_DATA TO WRITE: {fused_ki_data}")
                        agent.memory.save_ki(fused_ki_data)
                        
                        # 强一致原子删除清退桶内的旧碎片 KI
                        with db:
                            for old_item in bucket:
                                db.execute("DELETE FROM knowledge_items WHERE id = ?", (old_item["id"],))
                                db.execute("DELETE FROM ki_embeddings WHERE ki_id = ?", (old_item["id"],))
                                db.execute("DELETE FROM kis_fts WHERE ki_id = ?", (old_item["id"],))
                                
                        embed_text = f"标题: {fused_title}\n摘要: {fused_summary}\n正文: {fused_content}"
                        asyncio.create_task(agent.memory.save_ki_embedding(fused_id, embed_text))
                        
                        logger.info(f"🎉 Successfully fused {len(bucket)} items into single Master KI {fused_id} (version: {next_version})!")
                except Exception as e:
                    logger.error(f"❌ Failed to fuse bucket via LLM: {e}")
                    
    except Exception as e:
        logger.error(f"❌ [深夜全局熔炼异常] {e}")
