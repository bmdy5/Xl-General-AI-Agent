import time
import math
import re
from collections import OrderedDict

class MemoryCache:
    def __init__(self, capacity=50, ttl=30):
        self.cache = OrderedDict()
        self.capacity = capacity
        self.ttl = ttl
        self.hits = 0
        self.misses = 0
    
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0
    
    def get(self, key, query_vec=None, semantic_threshold=0.85):
        # 精确匹配阶段
        if key in self.cache:
            ts, val, emb = self.cache[key]
            if time.time() - ts <= self.ttl:
                self.hits += 1
                self.cache.move_to_end(key)
                return val
            else:
                del self.cache[key]

        # 语义匹配阶段（基于 embedding 余弦相似度）
        if query_vec is not None:
            best_key = None
            best_sim = 0.0
            best_val = None
            expired_keys = []
            for k, (ts, v, e) in self.cache.items():
                if e is None:
                    continue
                if time.time() - ts > self.ttl:
                    expired_keys.append(k)
                    continue
                sim = self._cosine_similarity(query_vec, e)
                if sim > best_sim:
                    best_sim = sim
                    best_key = k
                    best_val = v
            
            # 循环结束后统一物理移除过期条目，防范 RuntimeError
            for ek in expired_keys:
                self.cache.pop(ek, None)
                
            if best_sim >= semantic_threshold:
                self.hits += 1
                self.cache.move_to_end(best_key)
                return best_val

        self.misses += 1
        return None

    def set(self, key, val, embedding=None):
        self.cache[key] = (time.time(), val, embedding)
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    @staticmethod
    def _cosine_similarity(v1, v2):
        v1, v2 = list(v1), list(v2)
        dot = sum(a * b for a, b in zip(v1, v2))
        m1 = math.sqrt(sum(a * a for a in v1))
        m2 = math.sqrt(sum(b * b for b in v2))
        if m1 == 0 or m2 == 0:
            return 0.0
        return dot / (m1 * m2)
    
    def invalidate_all(self):
        self.cache.clear()
        
    def invalidate_keys(self, keywords=None, text=None):
        """高精度选择性失效：发现传入文本分词与缓存 query 的分词存在交集时剔除对应 Key"""
        if not keywords and not text:
            self.cache.clear()
            return
            
        def get_tokens(s):
            if not s:
                return set()
            s = str(s).lower()
            tokens = set()
            # 提取英文单词 (长度>=3)
            en_words = re.findall(r'[a-zA-Z]{3,}', s)
            tokens.update(en_words)
            # 提取中文 bigram (连续两个中文字符)
            zh_chars = re.findall(r'[\u4e00-\u9fff]', s)
            for i in range(len(zh_chars) - 1):
                tokens.add(zh_chars[i] + zh_chars[i + 1])
            return tokens

        target_words = set()
        if keywords:
            if isinstance(keywords, str):
                target_words.update(get_tokens(keywords))
            elif isinstance(keywords, list):
                for kw in keywords:
                    target_words.update(get_tokens(kw))
        if text:
            target_words.update(get_tokens(text))
            
        if not target_words:
            # 提取不出分词时，兜底清空全部
            self.cache.clear()
            return
            
        keys_to_del = []
        for key in list(self.cache.keys()):
            query = key[0] if isinstance(key, (tuple, list)) else key
            query_words = get_tokens(str(query).lower())
            if query_words.intersection(target_words):
                keys_to_del.append(key)
                
        for key in keys_to_del:
            del self.cache[key]
