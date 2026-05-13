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

logger = logging.getLogger(__name__)

KNOWLEDGE_BASE = Path(
    os.getenv("MYAGENT_KB_DIR", "/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西")
)

CATEGORIES = ["后端", "前端", "AI", "运维", "技能"]

# LLM 分析文章的 prompt
ANALYZE_PROMPT = """你是一个知识提取专家。请分析以下网页文章，提取有价值的知识。

## 文章内容
{content}

## 请按以下 JSON 格式输出（只输出 JSON，不要其他内容）：
{{
  "title": "文章标题（中文，20字以内）",
  "category": "后端/前端/AI/运维 之一",
  "key_insights": ["核心发现1", "核心发现2", "核心发现3"],
  "summary": "3-5条要点提炼，每条不超过30字，只写下次真能用上的",
  "is_skill": true/false,
  "skill_score": 1-10,
  "skill_name": "如果is_skill为true，给技能起一个名字",
  "skill_steps": ["如果is_skill为true，列出3-5个关键步骤"]
}}

## 分类规则
- 后端: API、数据库、服务器、Python/FastAPI/Go/Rust、性能优化
- 前端: React/Vue/TypeScript/CSS/UI设计/Next.js
- AI: LLM、Agent、机器学习、Prompt工程、RAG、向量数据库
- 运维: Docker/K8s、部署、CI/CD、Linux、监控

## 技能判断规则（从严）
is_skill 为 true 需要同时满足三个条件：
1. 有明确的、可复用的步骤（不是"最佳实践"这种泛泛而谈）
2. 下次遇到同类任务可以直接按这个流程操作，不用再想
3. 步骤必须具体（"用 Ruff check 检查代码风格"而不是"注意代码风格"）

以下情况 is_skill 必须为 false：
- PEP 8、代码风格、注释规范 → 不是技能，是常识
- "学习路线"、"入门指南" → 不是技能，是教程大纲
- 纯概念解释（"什么是闭包"） → 是知识点不是技能
- 商业推广、课程介绍 → 垃圾

skill_score 评分：
- 8-10：高度可复用，步骤具体可执行（如"部署检查清单"）
- 5-7：有参考价值但不够实操
- 1-4：不应创建技能

**默认 is_skill=false，只有非常确定才给 true。**

只输出 JSON，不要其他任何文字。"""

# Pro 模型审查 prompt
REVIEW_PROMPT = """你是一个知识质量控制专家。flash 模型从文章中提取了一些知识，请审查是否值得存入长期记忆。

## flash 提取的内容
标题: {title}
分类: {category}
核心发现: {insights}
摘要: {summary}
是否创建技能: {is_skill}
技能评分: {skill_score}
技能步骤: {skill_steps}

## 审查标准（极严，默认不通过）
**以下情况直接拒绝，不要犹豫：**
- 商业推广（课程、社区、工具推销）→ approved=false
- 过时内容（2025年及以前的"最新"对比/排名）→ approved=false
- 没有具体代码/命令/数字 → approved=false
- 纯介绍、纯概念、没有操作步骤 → approved=false
- 信息太稀（500字就一句话有用）→ approved=false
- bit.ly、短链接、推广链接 → approved=false
- "零基础也可"、"无需编程" → approved=false（营销话术）
- 内容少于200字 → approved=false

**只有同时满足才通过：**
1. 包含具体代码/命令/配置/数字
2. 下次真实会遇到并打开看
3. 不是常识，不是营销

## 请按以下 JSON 输出
{{
  "approved": true/false,
  "reason": "一句话说明通过/不通过的原因",
  "corrected_insights": ["如果原insight不够准确，修正后的版本。否则用原版"],
  "create_skill": true/false,
  "final_skill_score": 1-10
}}

## 记忆审查规则
- approved=false: 小众知识、广告软文、不准确内容、老生常谈
- approved=true: 通用且可操作的实用知识
- create_skill: 仅当技能评分>=7且确实可复用时才为true

只输出 JSON，不要其他文字。"""


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
        roles = await self._generate_debate_roles(" + ".join(topics[:2]))
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
                q = await self._cross_critique(r["name"], f.get("title", ""), {"insights": insights})
                critiques.append(q)
            devil_q = await self._devil_question(roles[2]["name"], {"insights": insights}, f.get("topic", ""))
            critiques.append(devil_q)

            # 辩护
            rebuttal = await self._rebut(roles[0]["name"], {"insights": insights, "critiques": critiques})

            # 双评审独立打分
            scores = [None, None]
            for i in range(2):
                scores[i] = await self._score_single(roles[i]["name"], f.get("title", ""), {"insights": insights, "critiques": critiques, "rebuttal": rebuttal})

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
        return await self._ask_json(
            f"审查这条学习发现，判断是否值得存入知识库：\n"
            f"标题: {f.get('title','')}\n"
            f"分类: {f.get('category','AI')}\n"
            f"要点: {json.dumps(f.get('insights',[]), ensure_ascii=False)[:500]}\n\n"
            "标准：通用性、可操作性、准确性、非商业推广。\n"
            'JSON: {"approved":true/false,"reason":"","corrected_insights":["要点"]}',
            use_review=True
        )

    # ── 辩论方法 ──────────────────────────────────────────

    async def _generate_debate_roles(self, topic: str) -> list[dict]:
        return await self._ask_json_list(
            f"为学习'{topic}'生成3个角色:\n"
            "1. 激进派专家 2. 保守派专家 3. 魔鬼代言人(只管找漏洞)\n"
            'JSON: [{"name":"","emoji":"","perspective":""}]'
        )

    async def _cross_critique(self, name: str, title: str, findings: dict) -> str:
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": (
                    f"你是{name}。质疑'{title}': "
                    f"{json.dumps(findings.get('insights', []), ensure_ascii=False)[:500]}\n30字。"
                )}], tools=None, model_override=self._learn_model)
            return resp.get("content", "").strip()[:120]
        except Exception: return ""

    async def _devil_question(self, name: str, findings: dict, topic: str) -> str:
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": (
                    f"你是'{name}'，专门找漏洞。质疑关于'{topic}'的发现: "
                    f"{json.dumps(findings.get('insights', []), ensure_ascii=False)[:500]}"
                    f"——最致命漏洞？30字。"
                )}], tools=None, model_override=self._learn_model)
            return resp.get("content", "").strip()[:120]
        except Exception: return ""

    async def _rebut(self, name: str, findings: dict) -> str:
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": (
                    f"你是'{name}'。质疑: {'; '.join(findings.get('critiques', []))[:200]}\n"
                    f"反驳。40字。"
                )}], tools=None, model_override=self._learn_model)
            return resp.get("content", "").strip()[:120]
        except Exception: return ""

    async def _score_single(self, name: str, topic: str, findings: dict) -> Optional[dict]:
        return await self._ask_json(
            f"评审'{name}'关于'{topic}'的发现:\n"
            f"{json.dumps(findings.get('insights', []), ensure_ascii=False)[:800]}\n"
            f"质疑: {'; '.join(findings.get('critiques', []))[:200]}\n"
            f"反驳: {findings.get('rebuttal', '')[:100]}\n"
            "打分 practicality(实用性) accuracy(准确性) 1-10。JSON: {\"practicality\":N,\"accuracy\":N,\"reason\":\"\"}",
            use_review=True
        )

    async def _ask_json(self, prompt: str, use_review: bool = False) -> Optional[dict]:
        """统一 LLM→JSON 调用，消除 8 处重复 try/except."""
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                model_override=self._review_model if use_review else self._learn_model,
            )
            text = resp.get("content", "").strip()
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception:
            pass
        return None

    async def _ask_json_list(self, prompt: str) -> list:
        """统一 LLM→JSON数组 调用."""
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None, model_override=self._learn_model,
            )
            text = resp.get("content", "").strip()
            json_match = re.search(r'\[[\s\S]*\]', text)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception:
            pass
        return []

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
