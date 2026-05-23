"""自主学习模块 v2 — LLM 真正参与学习流程.

流程：
  1. 从记忆库提取学习兴趣
  2. web_search 搜相关话题
  3. web_fetch 读文章全文
  4. LLM 精读 → 提取关键知识 → 分类 → 决定是否创建技能
  5. 保存到知识库 + agent 记忆
  6. 循环 20 分钟
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .debate import ANALYZE_PROMPT, REVIEW_PROMPT

logger = logging.getLogger(__name__)

KNOWLEDGE_BASE = Path(
    os.getenv("MYAGENT_KB_DIR", "/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西")
)

CATEGORIES = ["后端", "前端", "AI", "运维", "技能"]





class AutoLearner:
    """自主学习器 v2 — LLM 参与全过程."""

    LOCAL_PATHS = [
        Path("/Users/xiaofeng/Documents/gpt-image2中转站"),
        Path("/Users/xiaofeng/Documents/个人博客/学习笔记/源码集合/agent源码"),
        Path("/Users/xiaofeng/Documents/个人博客/学习笔记"),
    ]

    def __init__(self, agent, max_duration_minutes: int = 5, learn_model: str = "", dashboard=None):
        self.agent = agent
        self.max_duration = max_duration_minutes * 60
        self.kb = KNOWLEDGE_BASE
        self._seen_topics: set = set()
        self._seen_urls: set = set()
        self._seen_files: set = set()
        self._learn_model = learn_model or agent.llm.model
        self._review_model = os.getenv("MYAGENT_REVIEW_MODEL") or agent.llm.model
        
        from .debate import DebateSystem
        self.debate_system = DebateSystem(agent, self._learn_model, self._review_model)
        
        self._agent_scores: dict = {}
        self._dash = dashboard  # 可选 dashboard
        self._ensure_dirs()

    async def _dash_event(self, agent_id: str, event: str, **extra):
        """推送事件到 dashboard（如果已连接）."""
        if self._dash:
            await self._dash.send({"agent": agent_id, "event": event, **extra})

    async def run(self) -> dict:
        """子代理学习: 拆分任务→spawn子代理并行→回收审查→入库."""
        start_time = asyncio.get_event_loop().time()
        articles_read = 0
        skills_created = 0
        topics_learned = []
        errors = []

        interests = self._get_interests()
        if not interests:
            return {"articles_read": 0, "skills_created": 0, "topics": [], "summary": "无学习主题", "errors": []}

        # 拆分为 3 个方向
        directions = self._split_directions(interests)
        print(f"\n  🎯 学习方向: {directions}")

        # Phase 1: spawn 子代理并行学习
        print(f"  🤖 派发 {len(directions)} 个子代理...")
        tasks = []
        for d in directions:
            tasks.append(asyncio.wait_for(
                self._spawn_learn(d), timeout=180
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_findings = []
        for i, r in enumerate(results):
            if isinstance(r, dict):
                all_findings.extend(r.get("findings", []))
                print(f"  ✅ {directions[i]}: {r.get('count', 0)} 条")
            elif isinstance(r, Exception):
                errors.append(f"{directions[i]}: {r}")
                print(f"  ❌ {directions[i]}: {r}")

        if not all_findings:
            return {"articles_read": 0, "skills_created": 0, "topics": [], "summary": "无发现", "errors": errors}

        # Phase 2: 辩论 + 双审查
        print(f"\n  ⚔️ Phase 2: 辩论审查 {len(all_findings)} 条发现...")
        topics = list({f.get("topic", f.get("title", "")) for f in all_findings})
        roles = await self.debate_system.generate_debate_roles(" + ".join(topics[:2]))
        if len(roles) >= 3:
            print(f"     {roles[0]['emoji']} {roles[0]['name']} vs {roles[1]['emoji']} {roles[1]['name']} + 👿{roles[2]['name']}")

        for f in all_findings:
            if (asyncio.get_event_loop().time() - start_time) >= self.max_duration:
                break
            insights = f.get("insights", [])
            if not insights: continue

            # 交叉质疑
            critiques = []
            for r in roles[:2]:
                q = await self.debate_system.cross_critique(r["name"], f.get("title", ""), {"insights": insights})
                critiques.append(q)
            devil_q = await self.debate_system.devil_question(roles[2]["name"], {"insights": insights}, f.get("topic", ""))
            critiques.append(devil_q)

            # 辩护
            rebuttal = await self.debate_system.rebut(roles[0]["name"], {"insights": insights, "critiques": critiques})

            # 双评审独立打分
            scores = [None, None]
            for i in range(2):
                scores[i] = await self.debate_system.score_single(roles[i]["name"], f.get("title", ""), {"insights": insights, "critiques": critiques, "rebuttal": rebuttal})

            # 都通过才入库
            if scores[0] and scores[1]:
                avg = (scores[0].get("practicality", 0) + scores[0].get("accuracy", 0) +
                       scores[1].get("practicality", 0) + scores[1].get("accuracy", 0)) / 4
                if avg >= 7:
                    cat = f.get("category", "AI")
                    title = f.get("title", "untitled")
                    filename = self._safe_filename(title)
                    filepath = self.kb / cat / f"{filename}.md"
                    filepath.write_text(f"# {title}\n\n**来源**: {f.get('source', '')}\n**评分**: {avg:.1f}/10\n\n## 要点\n" + "\n".join(f"- {i}" for i in insights))
                    try:
                        await self.agent.memory.save(f"learn_{filename}", f"[learn] {cat}: {insights[0][:80]}",
                            f"来源: {f.get('source', '')}\n辩论评分: {avg:.1f}/10\n" + "\n".join(f"- {i}" for i in insights))
                    except Exception: pass
                    articles_read += 1
                    topics_learned.append(f"{cat}/{title}")
                    print(f"  ✅ {title[:40]} ({avg:.1f})")
                    continue
            print(f"  ❌ {f.get('title', '?')[:40]}")

        summary = await self._generate_summary(articles_read, skills_created, topics_learned, errors)
        return {"articles_read": articles_read, "skills_created": skills_created,
                "topics": topics_learned, "summary": summary, "errors": errors}

    def _split_directions(self, interests: list) -> list:
        """把兴趣拆成 3 个学习方向."""
        if len(interests) <= 3:
            return interests
        return [interests[0], interests[len(interests)//2], interests[-1]]

    async def _spawn_learn(self, topic: str) -> dict:
        """spawn 一个 coder 子代理去学一个主题."""
        from agent.tools.spawn_agent_tool import SpawnAgentTool
        tool = SpawnAgentTool()

        task = (
            f"搜索并学习关于 '{topic}' 的最新知识（2025-2026）。\n"
            f"要求：\n"
            f"1. 用 web_search 搜 1-2 篇高质量文章\n"
            f"2. 用 web_fetch 读文章\n"
            f"3. 提取 3 条核心要点\n"
            f"4. 用以下 JSON 格式返回（只返回 JSON）：\n"
            f'{{"title":"知识点","category":"AI/后端/前端/运维","insights":["要点1","要点2","要点3"],"source":"URL"}}'
        )

        findings = []
        async for tr in tool.call({"role": "general", "task": task, "context": ""}, context=self.agent):
            if tr.type == "result":
                text = str(tr.data)
                # 提取 JSON
                import re
                json_match = re.search(r'\{[\s\S]*\}', text)
                if json_match:
                    try:
                        findings.append(json.loads(json_match.group(0)))
                    except json.JSONDecodeError:
                        pass
        return {"findings": findings, "count": len(findings)}

    async def _review_finding(self, f: dict) -> Optional[dict]:
        """主 agent 审查一条发现."""
        return await self.debate_system._ask_json(
            f"审查这条学习发现，判断是否值得存入知识库：\n"
            f"标题: {f.get('title','')}\n"
            f"分类: {f.get('category','AI')}\n"
            f"要点: {json.dumps(f.get('insights',[]), ensure_ascii=False)[:500]}\n\n"
            "标准：通用性、可操作性、准确性、非商业推广。\n"
            'JSON: {"approved":true/false,"reason":"","corrected_insights":["要点"]}',
            use_review=True
        )

    # ── 辩论学习系统 ──────────────────────────────────────

    async def _call_tool(self, tool_name: str, args: dict) -> Optional[str]:
        """直接调用工具（web_search / web_fetch 不需要 LLM 参与）."""
        tool = self.agent.registry.get(tool_name)
        if not tool:
            return None
        try:
            async for tr in tool.call(args, context=self.agent):
                if tr.type == "result":
                    return str(tr.data)
        except Exception as e:
            logger.warning(f"Tool {tool_name} failed: {e}")
        return None

    # ── 辅助方法 ──────────────────────────────────────────────

    def _get_interests(self) -> list[str]:
        """从记忆库提取学习兴趣：反馈驱动（60%）+ 兴趣驱动（40%）."""
        gaps = []    # 用户不满/纠正的地方 → 优先学
        interests = []  # 用户关注的技术栈

        try:
            entries = self.agent.memory._parse_index()

            for e in entries:
                desc = e.get("description", "")
                fname = e.get("filename", "")
                combined = f"{desc} {fname}".lower()

                # 反馈驱动：用户纠正过的地方
                if "[feedback]" in desc or "feedback" in fname:
                    # 从反馈中提取学习主题
                    topic = self._extract_gap_topic(desc)
                    if topic and topic not in gaps:
                        gaps.append(topic)

                # 兴趣驱动：用户关注的技术栈
                tech_kw = [
                    "python", "fastapi", "react", "docker", "ai", "agent",
                    "llm", "kubernetes", "typescript", "golang", "rust",
                    "nextjs", "vue", "database", "api", "frontend", "backend",
                    "devops", "linux", "security", "performance",
                ]
                for kw in tech_kw:
                    if kw in combined and kw not in interests:
                        interests.append(kw)

        except Exception:
            pass

        # 反馈优先占 60%，兴趣占 40%
        topics = gaps[:3] if gaps else []
        for t in interests:
            if t not in topics:
                topics.append(t)
            if len(topics) >= 5:
                break

        if not topics:
            topics = ["Python", "AI agent", "LLM", "FastAPI"]

        self._gaps = gaps
        if gaps:
            print(f"  🎯 反馈驱动: {gaps[:3]}")
        print(f"  📚 兴趣驱动: {topics[len(gaps[:3]):]}")
        return topics

    def _extract_gap_topic(self, feedback_desc: str) -> str:
        """从反馈中提取应该学习的话题.

        "测试要用真实数据库不要mock" → "database testing best practices"
        "日志不要用f-string" → "Python logging best practices"
        """
        mapping = {
            "测试": "testing best practices",
            "mock": "integration testing",
            "日志": "logging best practices",
            "f-string": "Python logging",
            "部署": "deployment automation",
            "docker": "Docker best practices",
            "安全": "security best practices",
            "性能": "performance optimization",
            "数据库": "database optimization",
            "api": "API design best practices",
            "错误": "error handling patterns",
            "异步": "async programming patterns",
            "并发": "concurrency patterns",
        }
        desc_lower = feedback_desc.lower()
        for cn, en in mapping.items():
            if cn in desc_lower:
                return en
        # 提取前几个有意义的词
        cleaned = feedback_desc.replace("[feedback]", "").strip()
        return cleaned[:60] if cleaned else ""

    def _extract_urls(self, text: str) -> list[str]:
        urls = re.findall(r'https?://[^\s,)\]]+', text)
        skip = ["youtube.com", "github.com/", "twitter.com", "x.com", "linkedin.com"]
        return [u for u in urls if not any(s in u for s in skip)]

    def _safe_filename(self, title: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        clean = re.sub(r'[^\w一-鿿-]', '_', title)[:50]
        return f"{ts}-{clean}"

    async def _generate_summary(
        self, articles: int, skills: int, topics: list, errors: list
    ) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        topics_md = "\n".join(f"- {t}" for t in topics) if topics else "- 无"
        errors_md = "\n".join(f"- {e}" for e in errors) if errors else "✅ 无错误"

        summary = (
            f"# 自主学习摘要\n\n"
            f"**时间**: {now}\n"
            f"**阅读文章**: {articles} 篇\n"
            f"**创建技能**: {skills} 个\n\n"
            f"## 学习主题\n{topics_md}\n\n"
            f"## 错误\n{errors_md}\n"
        )

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (self.kb / f"每日摘要-{ts}.md").write_text(summary, encoding="utf-8")
        return summary

    def _ensure_dirs(self):
        for cat in CATEGORIES:
            (self.kb / cat).mkdir(parents=True, exist_ok=True)
