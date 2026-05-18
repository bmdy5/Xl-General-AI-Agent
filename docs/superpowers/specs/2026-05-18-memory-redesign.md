# Memory System Redesign

2026-05-18 | Status: approved

## Problems

1. **压缩记忆丢失**: compressor 生成摘要后只放回 `self.messages`，不持久化。Gateway 重启即丢。
2. **记忆散乱**: `~/.my-agent/memory/` 40+ 个 `.md` 文件，无分类、无去重、无过期清理。
3. **记忆与笔记割裂**: `save_memory` 写到 `~/.my-agent/memory/`，知识没归档到 `学习笔记/`。
4. **错误无闭环**: bot 命令出错后闷头重试，不记录、不自修、不汇报。
5. **索引脆弱**: 笔记移动后记忆里的路径断链，无法自愈。

## Architecture: Two-Tier + Error Loop

### Tier 1: Core Memory (`~/.my-agent/memory/`)

**人格底线，不依赖学习笔记**。笔记没了 agent 也不能废。

Contents:
- personality.md — 性格底色、用户关系
- feedback_*.md — 行为纠正（不清理）
- error_log.md — 错误配方库
- routing_rules.md — 知识路由规则（bot 自己维护）
- MEMORY.md — 核心记忆索引 + 知识指针

### Tier 2: Knowledge Index

MEMORY.md 中的知识条目只存指针：
```
- [web_fetch 代理超时问题] → 02-Agent技术/工具系统/web_fetch代理问题.md
```

实际内容存在学习笔记对应目录。路由由 `routing_rules.md` 定义，不硬编码。

### Tier 3: Error Loop

```
工具报错
  → 去重: 5min 内同类错误 → 静默记录，不重复汇报
  → 分级:
     L1-瞬态 (SSL超时/连接重置) → 等 3s 重试最多 2 次
     L2-可自修 (路径错误/模块缺失) → 匹配已知配方 → 自动修复
     L3-致命 (权限拒绝/磁盘满) → 立刻汇报
  → 修复成功 → 保存配方到 error_log.md
  → 连续 3 次同类型 → 触发 pattern 分析 → 建议根治方案
  → 2 轮仍失败 → 汇报亮哥: "X 失败，原因 Y，试了 A、B"
```

## Files Changed

### agent/memory/manager.py
- Add `routing_rules` loading
- Add `save_to_notes(section, filename, content)` — write to learning notes
- Add `save_index(entry, note_path)` — write pointer to MEMORY.md
- Add self-healing: check index → fix broken links

### agent/compressor.py
- After successful compression: auto-call `memory.save("compressed_<date>", ...)` 
- Summary saved as `compressed_YYYY-MM-DD.md`

### agent/core.py (new: error_tracker.py)
- Error dedup map (error_type + timestamp)
- L1/L2/L3 classifier
- Auto-retry with backoff
- Recipe matching from error_log.md
- Escalation to user

### agent/gateway.py
- On agent init, pass session_key as session_id (already done)
- Inject error_log.md into memory context

### routing_rules.md (new file)
```
# 知识路由规则
学习笔记根目录: /Users/xiaofeng/Desktop/学习笔记
## 路由判断
1. 跟小萤自身相关 → 01-小萤/自学习笔记/
2. Agent 通用技术 → 02-Agent技术/
3. 项目经验 → 06-工作记录/工程实践/
```

## Non-Goals
- No hardcoded knowledge paths in Python
- No automatic memory deletion (bot doesn't decide what to forget)
- No changes to learning notes directory structure
