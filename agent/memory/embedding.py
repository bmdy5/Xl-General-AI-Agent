import os
import time
import logging
import asyncio
from pathlib import Path

logger = logging.getLogger("agent.memory.embedding")

async def _get_embedding(manager, text: str) -> list[float]:
    """提取 768 维中文增强语义向量。支持本地 m3e-base 或远程 API 两种模式。"""
    text = text.strip()[:300]
    embedding_mode = os.getenv("EMBEDDING_MODE", "local").lower()

    if embedding_mode == "local":
        try:
            global _LOCAL_MODEL_CACHE
            if "_LOCAL_MODEL_CACHE" not in globals():
                globals()["_LOCAL_MODEL_CACHE"] = {}
            
            cache = globals()["_LOCAL_MODEL_CACHE"]
            
            if "_m3e" in cache and cache["_m3e"] is None:
                last_fail = cache.get("_last_fail_time", 0.0)
                if time.time() - last_fail < 60.0:
                    return [0.0] * 768
                else:
                    # 冷却结束，清除熔断状态重新尝试加载
                    cache.pop("_m3e", None)
            
            if "_m3e" not in cache:
                from agent.core.config import settings
                memory_cfg = settings.get("memory") or {}
                model_path_str = memory_cfg.get("local_model_path", "./model/m3e-base")
                local_model_path = manager.resolve_adaptive_path(model_path_str)
                
                if not (local_model_path.exists() and local_model_path.is_dir()):
                    cache["_m3e"] = None
                    cache["_last_fail_time"] = time.time()
                    logger.error(f"Offline model path not found at {local_model_path}. Circuit breaker activated instantly. 0ms fallback to zeros.")
                    return [0.0] * 768
                
                from sentence_transformers import SentenceTransformer
                
                try:
                    logger.info(f"Loading m3e-base model from local path: {local_model_path}")
                    model = await asyncio.wait_for(
                        asyncio.to_thread(SentenceTransformer, str(local_model_path)),
                        timeout=10.0
                    )
                    cache["_m3e"] = model
                    logger.info("Local m3e-base model loaded successfully!")
                except Exception as load_err:
                    cache["_m3e"] = None
                    cache["_last_fail_time"] = time.time()
                    logger.error(f"Failed to load m3e-base model: {load_err}. Circuit breaker activated, local embedding disabled.")
                    return [0.0] * 768
            
            model = cache["_m3e"]
            if model is None:
                return [0.0] * 768
            
            embeddings = await asyncio.wait_for(
                asyncio.to_thread(model.encode, [text], show_progress_bar=False),
                timeout=10.0
            )
            return [float(x) for x in embeddings[0]]
        except Exception as e:
            logger.error(f"Failed to extract local embedding via m3e-base: {e}")
            return [0.0] * 768

    import litellm
    model = os.getenv("MYAGENT_EMBEDDING_MODEL", "text-embedding-3-small")
    api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    api_base = os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE") or None
    
    try:
        response = await litellm.aembedding(
            model=model,
            input=[text],
            dimensions=768,
            api_key=api_key if api_key else None,
            api_base=api_base if api_base else None
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Failed to fetch embedding: {e}")
        return [0.0] * 768


async def save_ki_embedding(manager, ki_id: str, text_to_embed: str):
    """后台异步协程任务：非阻塞为指定 KI 提取 768 维 Embedding 并原子保存至 SQLite。"""
    embedding = await manager._get_embedding(text_to_embed)
    import json
    embedding_str = json.dumps(embedding)
    db = manager._get_db()
    with db:
        db.execute("""
            INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding)
            VALUES (?, ?)
        """, (ki_id, embedding_str))
