"""笔记知识库 FTS5 索引 — PageIndex 全文检索（不切块）。

索引 /Users/xiaofeng/Desktop/学习笔记/ 下的 .md 文件。
每个文件一行，BM25 全文匹配，LLM 用 read_file 精读结果。
"""

import os
import re
import sqlite3
import time
import ipaddress
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

NOTES_DIR = Path("/Users/xiaofeng/Desktop/学习笔记")

# 使用 100% 绝对无乱码的显式 Unicode 转义码点范围
_CJK_RE = re.compile(r'([\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef])')

def _cjk_space(text: str) -> str:
    """Insert spaces around CJK characters so unicode61 tokenizer splits each char."""
    if not text:
        return ""
    return _CJK_RE.sub(r' \1 ', text)

def _restore_cjk(text: str) -> str:
    """Restore text by removing injected spaces around CJK characters."""
    if not text:
        return ""
    text = re.sub(r'\s*([一-鿿㐀-䶿〿-㿿＀-￯])\s*', r'\1', text)
    return re.sub(r'\s+', ' ', text).strip()


def create_table(conn: sqlite3.Connection):
    """创建 PageIndex FTS5 表和 metadata 伴随表。"""
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
        USING fts5(content, title, path, directory,
                   tokenize="porter unicode61")
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes_meta (
            path TEXT PRIMARY KEY,
            mtime REAL
        )
    """)
    # 自动平滑过渡：若新 meta 表为空但 fts 里有老无空格 data，清空 fts 表以触发全新增量索引
    try:
        meta_count = conn.execute("SELECT COUNT(*) FROM notes_meta").fetchone()[0]
        if meta_count == 0:
            conn.execute("DELETE FROM notes_fts")
            conn.commit()
    except Exception:
        pass


def scan_files(base: Path = NOTES_DIR) -> list[dict]:
    """扫描目录下所有 .md 文件，返回 [{path, title, directory}]."""
    results = []
    if not base.exists():
        return results
    for f in sorted(base.rglob("*.md")):
        if ".obsidian" in str(f) or ".DS_Store" in str(f):
            continue
        rel = f.relative_to(base)
        results.append({
            "path": str(rel),
            "title": f.stem,
            "directory": str(rel.parent) if str(rel.parent) != "." else "",
            "full_path": str(f),
            "mtime": os.path.getmtime(f),
        })
    return results


def sync_incremental(conn: sqlite3.Connection, base: Path = NOTES_DIR) -> int:
    """增量同步：比对 mtime，新增/更新/删除 FTS5 索引."""
    create_table(conn)
    if not base.exists():
        return 0

    disk_files = scan_files(base)
    disk_map = {f["path"]: f for f in disk_files}

    db_meta = {}
    try:
        cur = conn.execute("SELECT path, mtime FROM notes_meta")
        for row in cur:
            db_meta[row[0]] = row[1]
    except Exception:
        pass

    changes_count = 0

    # 1. 扫描磁盘，处理新增和修改的文件
    for path, df in disk_map.items():
        mtime = df["mtime"]
        full_path = df["full_path"]

        title_cjk = _cjk_space(df["title"])
        path_cjk = _cjk_space(df["path"])
        dir_cjk = _cjk_space(df["directory"])

        if path not in db_meta:
            # 新增文件
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    raw = fh.read()
                content_cjk = _cjk_space(raw[:10000])
                conn.execute(
                    "INSERT INTO notes_fts(content, title, path, directory) VALUES (?, ?, ?, ?)",
                    (content_cjk, title_cjk, path_cjk, dir_cjk)
                )
                conn.execute(
                    "INSERT INTO notes_meta(path, mtime) VALUES (?, ?)",
                    (path, mtime)
                )
                changes_count += 1
            except Exception:
                continue
        elif mtime > db_meta[path]:
            # 修改文件：先删除 fts（基于 CJK 化的 path 匹配），再插入
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    raw = fh.read()
                content_cjk = _cjk_space(raw[:10000])
                conn.execute("DELETE FROM notes_fts WHERE path = ?", (path_cjk,))
                conn.execute(
                    "INSERT INTO notes_fts(content, title, path, directory) VALUES (?, ?, ?, ?)",
                    (content_cjk, title_cjk, path_cjk, dir_cjk)
                )
                conn.execute(
                    "UPDATE notes_meta SET mtime = ? WHERE path = ?",
                    (mtime, path)
                )
                changes_count += 1
            except Exception:
                continue

    # 2. 扫描数据库，处理已在磁盘被删除的文件
    deleted_paths = [p for p in db_meta if p not in disk_map]
    for p in deleted_paths:
        p_cjk = _cjk_space(p)
        conn.execute("DELETE FROM notes_fts WHERE path = ?", (p_cjk,))
        conn.execute("DELETE FROM notes_meta WHERE path = ?", (p,))
        changes_count += 1

    if changes_count > 0:
        conn.commit()

    return changes_count


def index_all(conn: sqlite3.Connection, base: Path = NOTES_DIR) -> int:
    """PageIndex 全量重建：每文件一行，不切块。返回总文件数。"""
    create_table(conn)
    files = scan_files(base)
    conn.execute("DELETE FROM notes_fts")
    try:
        conn.execute("DELETE FROM notes_meta")
    except Exception:
        pass
    count = 0
    for f in files:
        try:
            with open(f["full_path"], "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except Exception:
            continue

        content_cjk = _cjk_space(raw[:10000])
        title_cjk = _cjk_space(f["title"][:200])
        path_cjk = _cjk_space(f["path"][:500])
        dir_cjk = _cjk_space(f["directory"][:500])

        conn.execute(
            "INSERT INTO notes_fts(content, title, path, directory) VALUES (?, ?, ?, ?)",
            (content_cjk, title_cjk, path_cjk, dir_cjk),
        )
        try:
            conn.execute(
                "INSERT INTO notes_meta(path, mtime) VALUES (?, ?)",
                (f["path"], f["mtime"])
            )
        except Exception:
            pass
        count += 1
    conn.commit()
    return count


def search(conn: sqlite3.Connection, query: str, limit: int = 5) -> list[dict]:
    """FTS5 全文搜索笔记库，BM25 排序。返回文件级匹配。"""
    clean = re.sub(r'[^\w一-鿿\s]', " ", query).strip()
    if not clean or len(clean) < 2:
        return []
    fts_query = " OR ".join(_cjk_space(clean).split())
    rows = conn.execute(
        """SELECT rowid, content, title, path, directory, rank
           FROM notes_fts WHERE notes_fts MATCH ?
           ORDER BY rank LIMIT ?""",
        (fts_query, limit),
    ).fetchall()
    return [
        {"id": r[0], "content": _restore_cjk(r[1][:200]), "title": _restore_cjk(r[2]),
         "path": _restore_cjk(r[3]), "directory": _restore_cjk(r[4]), "rank": r[5]}
        for r in rows
    ]


def rebuild(conn: sqlite3.Connection):
    """重建 FTS5 索引."""
    conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")


# ── 链接提取 ────────────────────────────────────────────────


def extract_urls(md_content: str) -> list[str]:
    """精准提取 Markdown 文本中的标准链接 [text](url) 以及裸露的 http/https 链接。

    支持 URL 中包含一层平衡括号（如 Wikipedia 风格的链接）。
    自动去重。
    """
    urls = []
    md_links = re.findall(
        r'\[[^\[\]]*\]\((https?://(?:[^\s\)(]|\([^\s\)]*\))+)\)',
        md_content
    )
    urls.extend(md_links)

    raw_links = re.findall(
        r'(?<!\()(https?://(?:[^\s\>\)\[(]|\([^\s\>\)\]]*\))+)',
        md_content
    )
    urls.extend(raw_links)

    return list(set(urls))


# ── 外链安全校验 ─────────────────────────────────────────


def is_safe_url(url: str) -> bool:
    """校验 URL 是否安全可 fetch：仅允许公开 http/https，拒绝内网地址。"""
    if not url.startswith(('http://', 'https://')):
        return False

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False

        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                return False
        except ValueError:
            pass

        hostname_lower = hostname.lower()
        if hostname_lower in ('localhost', 'localhost.localdomain'):
            return False
        if hostname_lower.endswith('.local') or hostname_lower.endswith('.internal'):
            return False
        if '.' not in hostname_lower:
            return False

        return True
    except Exception:
        return False


# ── 异步内存缓存 (TTL) ──────────────────────────────────

_CACHE_TTL = 14400       # 4 小时
_CACHE_MAXSIZE = 256
_link_summary_cache: OrderedDict = OrderedDict()


def _cache_get(url: str) -> str | None:
    """读取缓存，过期自动剔除。"""
    if url not in _link_summary_cache:
        return None
    value, expiry = _link_summary_cache[url]
    if time.time() > expiry:
        del _link_summary_cache[url]
        return None
    _link_summary_cache.move_to_end(url)
    return value


def _cache_set(url: str, summary: str | None) -> None:
    """写入缓存，超量淘汰最旧条目。"""
    _link_summary_cache[url] = (summary, time.time() + _CACHE_TTL)
    _link_summary_cache.move_to_end(url)
    while len(_link_summary_cache) > _CACHE_MAXSIZE:
        _link_summary_cache.popitem(last=False)


def _extract_text_sync(html: str) -> str:
    """同步提取网页文本（CPU 密集，放在线程池中执行）。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=' ', strip=True)[:3000]


# ── 链接总结入口 ─────────────────────────────────────────


async def get_link_summaries(note_paths: list[str], llm) -> str:
    """批量提取笔记中的外链，并调用 web_fetch 和 LLM 生成一句话摘要。

    改进（v2）：
    - aiohttp 替代 requests，彻底异步
    - BS4 解析放入线程池，不阻塞事件循环
    - TTL 缓存（4h）避免重复 fetch + LLM
    - is_safe_url 拦截内网/私网地址（防 SSRF）
    """
    if not note_paths:
        return ""

    all_urls = []
    for rel_path in note_paths:
        full_path = NOTES_DIR / str(rel_path)
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
                all_urls.extend(extract_urls(content))
        except Exception:
            continue

    all_urls = list(set(all_urls))
    safe_urls = [u for u in all_urls if is_safe_url(u)]
    if not safe_urls:
        return ""

    import asyncio
    import aiohttp

    summaries = []
    timeout = aiohttp.ClientTimeout(total=10)
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for url in safe_urls:
            cached = _cache_get(url)
            if cached is not None:
                if cached:
                    summaries.append(f"[链接摘要] {url} : {cached}")
                continue

            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        _cache_set(url, "")
                        continue
                    html = await resp.text()

                loop = asyncio.get_running_loop()
                text = await loop.run_in_executor(None, _extract_text_sync, html)
                if not text:
                    continue

                prompt = f"请用一句话总结以下网页内容：\n\n{text}"
                res = await llm.chat(messages=[{"role": "user", "content": prompt}])
                summary = res.get("content", "").strip()

                _cache_set(url, summary)

                if summary:
                    summaries.append(f"[链接摘要] {url} : {summary}")
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"获取链接 {url} 摘要失败: {e}")
                _cache_set(url, "")
                continue

    return "\n".join(summaries)
