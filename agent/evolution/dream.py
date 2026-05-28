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

from agent.memory.index import with_db_retry

@with_db_retry()
def _fuse_save_and_cleanup(agent, db, fused_ki_data, bucket):
    """原子写入熔炼后的 KI 并清除被融合的旧碎片 KI，具备 SQLite 写锁碰撞自适应退避重试能力."""
    with db:
        agent.memory.save_ki(fused_ki_data, _existing_db=db)
        for old_item in bucket:
            db.execute("DELETE FROM knowledge_items WHERE id = ?", (old_item["id"],))
            db.execute("DELETE FROM ki_embeddings WHERE ki_id = ?", (old_item["id"],))
            db.execute("DELETE FROM kis_fts WHERE ki_id = ?", (old_item["id"],))

from .dream_prompts import (
    DREAM_MERGE_PROMPT,
    DAMPING_JUDGE_PROMPT,
    DREAM_FUSE_PROMPT,
    DEEP_DREAM_KI_PROMPT,
    DEEP_DREAM_SKILL_PROMPT,
    DREAM_EVOLUTION_SUMMARY_PROMPT,
)

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

async def trigger_deep_dream_evolution(agent, history_messages=None) -> str:
    """深度长眠做梦进化主梦境协程：提炼全局 KI 并吞噬去重，检测 SOP 并突变合成新 Skill，返回做梦反思卡片内容"""
    logger.info("💤 [深度做梦开始] 正在读取全局长对话历史，进行深度脑力提炼与进化...")
    
    history_lines = []
    messages = history_messages if history_messages is not None else getattr(agent, "messages", [])
    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))
        history_lines.append(f"[{role}]: {content[:2000]}")
    
    history_text = "\n".join(history_lines)
    if not history_text.strip():
        logger.info("💤 [深度做梦取消] 对话历史为空。")
        return ""

    saved_learnings = []
    saved_skills = []

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
                    saved_learnings.append(learning)
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
                    import hashlib
                    trigger = result_skill.get("trigger", folder_name)
                    desc = result_skill.get("description", "会话自提炼与梦境进化得出的实战经验")
                    ki_id = f"dream_exp_{hashlib.md5(folder_name.encode()).hexdigest()[:12]}"
                    ki_data = {
                        "id": ki_id,
                        "title": folder_name,
                        "category": "experience",
                        "keywords": [kw.strip() for kw in trigger.replace("/", ",").split(",") if kw.strip()],
                        "summary": desc,
                        "content": md_content,
                        "ki_type": "experience",
                    }
                    agent.memory.save_ki(ki_data)
                    try:
                        await agent.memory.save_ki_embedding(ki_id, folder_name + " " + desc)
                    except Exception:
                        pass
                    saved_skills.append(skill_name)
                    logger.info(f"Experience saved to DB: {skill_name}")
    except Exception as e:
        logger.error(f"❌ [技能突变异常] 自进化合成 Skill 失败: {e}")

    # 2.5 🌙 深夜进化做梦收尾：自动唤醒脑区物理大蒸馏 GC，清退合并被吞噬的旧资产影子文件，防止磁盘臃肿
    try:
        cleaned = await agent.memory.gc_and_merge_fragmented_memories()
        if cleaned > 0:
            logger.info(f"🌙 [梦境大熔炼] 深夜大脱水自动清退了 {cleaned} 个被合并吞噬的旧资产影子文件！")
    except Exception as dream_gc_err:
        logger.error(f"Failed to run nightly dream GC: {dream_gc_err}")

    # 3. 生成做梦自省总结卡片 (高情商交互与 8s 容灾 Fallback)
    summary_card = ""
    if saved_learnings or saved_skills:
        ki_details_str = ""
        for i, kl in enumerate(saved_learnings):
            ki_details_str += f"{i+1}. 【{kl.get('title')}】({kl.get('category')}): {kl.get('summary')}\n   详细: {kl.get('content')}\n"
        
        skill_details_str = ""
        for i, sk in enumerate(saved_skills):
            skill_details_str += f"{i+1}. 技能【{sk}】\n"
            
        if not ki_details_str:
            ki_details_str = "(本次无新记忆事实提炼)\n"
        if not skill_details_str:
            skill_details_str = "(本次无新技能合成)\n"

        summary_prompt = DREAM_EVOLUTION_SUMMARY_PROMPT.format(
            ki_details=ki_details_str,
            skill_details=skill_details_str
        )
        
        try:
            # 8 秒硬超时保护
            response = await asyncio.wait_for(
                agent.llm.chat(
                    messages=[{"role": "user", "content": summary_prompt}],
                    tools=None,
                    model_override=agent.llm.model,
                ),
                timeout=8.0
            )
            summary_card = response.get("content", "").strip()
        except Exception as summary_err:
            logger.warning(f"Failed to generate high-EQ dream recap card via LLM: {summary_err}. Triggering Fallback template...")
            
        # Fallback 本地自愈模板
        if not summary_card:
            ki_count = len(saved_learnings)
            skill_count = len(saved_skills)
            skills_names_str = "、".join([f"【{sk}】" for sk in saved_skills]) if saved_skills else ""
            
            skill_section = f"新增了 {skills_names_str}" if skills_names_str else "无新增技能"
            
            summary_card = (
                "### 📊 梦境回顾总结 (系统离线提炼)\n\n"
                "由于大模型总结链接波动，小萤通过副脑为您快速整理了本次梦境精简成果：\n\n"
                "#### 💡 本次睡眠提炼的硬事实\n"
                f"* 提炼了 {ki_count} 条关于系统教训或用户偏好的核心记忆事实（已安全存入 SQLite memories.db 库）。\n\n"
                "#### 🛠️ 技能库突变状态\n"
                f"* 突变合成了 {skill_count} 个全新的自进化技能，已自动登记落盘至技能库 ({skill_section})。\n"
                "* 核心记忆库已完成去重吞噬熔接，算力已完全恢复 100% 满血状态！"
            )
            
    # 4. 深夜全局知识熔炼自演进 (方案 B)
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
            
            # 对每个 open 桶执行大模型熔炼
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
                        
                        # 使用带指数退避的重试机制，进行原子写入与碎片清理
                        _fuse_save_and_cleanup(agent, db, fused_ki_data, bucket)
                                
                        embed_text = f"标题: {fused_title}\n摘要: {fused_summary}\n正文: {fused_content}"
                        asyncio.create_task(agent.memory.save_ki_embedding(fused_id, embed_text))
                        
                        logger.info(f"🎉 Successfully fused {len(bucket)} items into single Master KI {fused_id} (version: {next_version})!")
                except Exception as e:
                    logger.error(f"❌ Failed to fuse bucket via LLM: {e}")
                    
    except Exception as e:
        logger.error(f"❌ [深夜全局熔炼异常] {e}")

    return summary_card
