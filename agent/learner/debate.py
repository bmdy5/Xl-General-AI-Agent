"""自主学习辩论与交叉质疑审查子系统 (从 auto_learn 物理剥离)"""

import json
import re
import logging

logger = logging.getLogger(__name__)

class DebateSystem:
    """激进派、保守派及魔鬼代言人三方交叉辩论质疑与打分审查算法."""
    
    def __init__(self, agent, learn_model: str, review_model: str):
        self.agent = agent
        self.learn_model = learn_model
        self.review_model = review_model
        
    async def generate_debate_roles(self, topic: str) -> list[dict]:
        """为学习主题动态分配三个不同立场的专家辩论角色."""
        prompt = (
            f"为学习'{topic}'生成3个角色:\n"
            "1. 激进派专家 2. 保守派专家 3. 魔鬼代言人(只管找漏洞)\n"
            'JSON: [{"name":"","emoji":"","perspective":""}]'
        )
        return await self._ask_json_list(prompt)

    async def cross_critique(self, name: str, title: str, findings: dict) -> str:
        """激进与保守两派专家进行针锋相对的学术质疑."""
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": (
                    f"你是{name}。质疑'{title}': "
                    f"{json.dumps(findings.get('insights', []), ensure_ascii=False)[:500]}\n30字。"
                )}], tools=None, model_override=self.learn_model)
            return resp.get("content", "").strip()[:120]
        except Exception:
            return ""

    async def devil_question(self, name: str, findings: dict, topic: str) -> str:
        """魔鬼代言人从最刻薄、刁钻的视角寻找核心理论漏洞."""
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": (
                    f"你是'{name}'，专门找漏洞。质疑关于'{topic}'的发现: "
                    f"{json.dumps(findings.get('insights', []), ensure_ascii=False)[:500]}"
                    f"——最致命漏洞？30字。"
                )}], tools=None, model_override=self.learn_model)
            return resp.get("content", "").strip()[:120]
        except Exception:
            return ""

    async def rebut(self, name: str, findings: dict) -> str:
        """辩护方专家针对所有质疑和挑衅进行正面学术反驳与自愈辩解."""
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": (
                    f"你是'{name}'。质疑: {'; '.join(findings.get('critiques', []))[:200]}\n"
                    f"反驳。40字。"
                )}], tools=None, model_override=self.learn_model)
            return resp.get("content", "").strip()[:120]
        except Exception:
            return ""

    async def score_single(self, name: str, topic: str, findings: dict) -> Optional = None:
        """双角色独立打分，评估实用性与准确性并说明论证理由."""
        prompt = (
            f"评审'{name}'关于'{topic}'的发现:\n"
            f"{json.dumps(findings.get('insights', []), ensure_ascii=False)[:800]}\n"
            f"质疑: {'; '.join(findings.get('critiques', []))[:200]}\n"
            f"反驳: {findings.get('rebuttal', '')[:100]}\n"
            "打分 practicality(实用性) accuracy(准确性) 1-10。JSON: {\"practicality\":N,\"accuracy\":N,\"reason\":\"\"}"
        )
        return await self._ask_json(prompt, use_review=True)

    async def _ask_json(self, prompt: str, use_review: bool = False) -> dict:
        """辅助方法：统一 LLM 调用并智能过滤解析 JSON 格式."""
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                model_override=self.review_model if use_review else self.learn_model,
            )
            text = resp.get("content", "").strip()
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception:
            pass
        return {}

    async def _ask_json_list(self, prompt: str) -> list:
        """辅助方法：统一调用并智能过滤解析 JSON 数组格式."""
        try:
            resp = await self.agent.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None, model_override=self.learn_model,
            )
            text = resp.get("content", "").strip()
            json_match = re.search(r'\[[\s\S]*\]', text)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception:
            pass
        return []

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
- PEP 8.. 代码风格、注释规范 → 不是技能，是常识
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
- 意图稀少（500字就一句话有用）→ approved=false
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
