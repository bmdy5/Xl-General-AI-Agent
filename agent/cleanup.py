"""知识库清理器 — pro 模型审查已有知识，删重、去杂、合并.

用法: python main.py --cleanup
"""

import asyncio
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_BASE = Path(
    "/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西"
)

# 清理审查 prompt
CLEANUP_PROMPT = """你是一个知识库管理员。请审查以下知识文件，决定是否保留。

## 文件列表（文件名 + 前200字）
{files}

## 审查标准（极严）
**默认删除。只有同时满足以下 3 条才保留：**
1. 包含具体、可操作的信息——代码/命令/配置/架构图/数字/工具名，至少有一项
2. 是你不知道的新知识，不是常识（"PEP 8 很重要" → 删）
3. 下次遇到同类问题时，会打开这个文件参考

**例外：以下情况没有代码也可以保留：**
- 架构设计决策（如"为什么选 Redis 而不是 Kafka"）
- 性能优化量化建议（如"连接池默认 2x CPU核数"）
- 安全规范（如"JWT 每次请求都要验证签名，不只是存 token"）
- 工具推荐有理由（如"uv 替代 pip 因为硬链接去重，快 10-100x"）

**以下情况直接删除：**
- PEP 8、代码风格、注释规范、变量命名 → 删
- 纯概念解释没有具体方案 → 删
- 教程大纲、学习路线 → 删
- 商业推广、课程介绍 → 删
- 泛泛的"最佳实践"没有任何数据或工具名 → 删

## 请输出 JSON 数组，每个文件一个决策
[
  {{"file": "文件名", "action": "keep|delete|merge", "merge_into": "如果要合并，合并到哪个文件", "reason": "一句话原因"}}
]

只输出 JSON 数组。"""


class KnowledgeCleaner:
    """知识库清理器."""

    def __init__(self, agent):
        self.agent = agent
        self.kb = KNOWLEDGE_BASE
        self.review_model = agent.llm.model

    async def run(self) -> dict:
        """执行清理."""
        print("\n  🧹 知识库清理开始\n")

        stats = {"deleted": 0, "kept": 0, "merged": 0, "skills_removed": 0}

        # 1. 清理每个分类目录
        for cat_dir in sorted(self.kb.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue

            files = list(cat_dir.glob("*.md"))
            if len(files) < 3:
                print(f"  {cat_dir.name}: {len(files)} 文件，跳过（太少）")
                continue

            print(f"\n  📂 {cat_dir.name}: {len(files)} 文件")

            # 分批审查（每批最多 15 个文件，避免 prompt 太长）
            for batch_start in range(0, len(files), 15):
                batch = files[batch_start:batch_start + 15]
                result = await self._review_batch(batch, cat_dir)
                stats["deleted"] += result["deleted"]
                stats["kept"] += result["kept"]
                stats["merged"] += result["merged"]
                if cat_dir.name == "技能":
                    stats["skills_removed"] += result["deleted"]

        # 2. 重建 agent 记忆
        await self._rebuild_memory()

        print(f"\n  ===== 清理完成 =====")
        print(f"  保留: {stats['kept']} | 删除: {stats['deleted']} | 合并: {stats['merged']}")
        print(f"  技能清理: {stats['skills_removed']} 个")
        return stats

    async def _review_batch(self, files: list, cat_dir: Path) -> dict:
        """审查一批文件."""
        # 构建文件摘要
        summaries = []
        for f in files:
            try:
                content = f.read_text(encoding="utf-8")[:200]
                summaries.append(f"### {f.name}\n{content}\n")
            except Exception:
                summaries.append(f"### {f.name}\n(无法读取)\n")

        prompt = CLEANUP_PROMPT.format(files="\n".join(summaries))

        try:
            response = await self.agent.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                model_override=self.review_model,
            )
            text = response.get("content", "").strip()
            json_match = re.search(r'\[[\s\S]*\]', text)
            if not json_match:
                return {"deleted": 0, "kept": len(files), "merged": 0}

            decisions = json.loads(json_match.group(0))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Batch review failed: {e}")
            return {"deleted": 0, "kept": len(files), "merged": 0}

        result = {"deleted": 0, "kept": 0, "merged": 0}
        for d in decisions:
            action = d.get("action", "keep")
            fname = d.get("file", "")
            reason = d.get("reason", "")

            if action == "delete":
                target = cat_dir / fname
                if target.exists():
                    target.unlink()
                    result["deleted"] += 1
                    print(f"    🗑 {fname}: {reason}")
            elif action == "merge":
                merge_into = d.get("merge_into", "")
                if merge_into:
                    print(f"    🔀 {fname} → {merge_into}: {reason}")
                    # 保留主文件，删重复
                    dup = cat_dir / fname
                    if dup.exists() and (cat_dir / merge_into).exists():
                        dup.unlink()
                        result["merged"] += 1
            else:
                result["kept"] += 1

        return result

    async def _rebuild_memory(self):
        """清理后重建 agent 记忆."""
        mem_dir = self.agent.memory.base_dir
        # 清空旧的 learn 类记忆
        for f in mem_dir.glob("learn_*.md"):
            f.unlink()
        # 重建 MEMORY.md
        entries = []
        for cat_dir in sorted(self.kb.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name in ("技能",) or cat_dir.name.startswith("."):
                continue
            for f in sorted(cat_dir.glob("*.md")):
                content = f.read_text(encoding="utf-8")[:500]
                title = f.stem.replace("_", " ")[:60]
                entries.append(f"- [{cat_dir.name} {title}]({f.name})")
        if entries:
            idx = "# Memory Index (cleaned)\n\n" + "\n".join(entries[:30])
            self.agent.memory.index_file.write_text(idx, encoding="utf-8")
            print(f"\n  📝 记忆重建: {len(entries[:30])} 条")


async def run_cleanup(agent):
    """入口：执行知识库清理."""
    cleaner = KnowledgeCleaner(agent)
    return await cleaner.run()
