# `agents/_seed` — 人格种子（Persona Seed）

这是随内核**发布**的 Eva 初始人格——「出生状态」。它**只含种子，不含任何活体记忆**。

`anima init` 会把本目录复制成用户私有的活体实例 `$ANIMA_HOME/agents/<name>`，
此后这个实例独立成长（feelings / growth_log / persona_state / lorebook / 记忆库），
**不再回流到内核仓库**。

## 种子包含（发布、只读）
- `identity/` — 我是谁（core / extended / personality / relationship / model_hints）
- `rules/` — 行为准则（boundaries / events / evolution / memory / output / safety / style / tools）
- `examples/` — 手写示范回复
- `post_processing/` — 风格后处理规则
- `config.yaml` / `manifest.yaml` / `soul.md` — agent 配置与（兼容用）灵魂总览
- `memory/feelings.md` · `memory/growth_log.md` — 空起点
- `memory/golden_replies.jsonl` — 空
- `lorebook/_index.yaml` — 空索引

## 种子**不**包含（属于私有活体实例）
- 带时间戳的情绪日记 / 成长记录 / 进化后的人格数值
- 学习到的 lorebook 条目、记忆库（`data/anima.db`、`chroma/`）
- 习得的技能（`skills/`）

> 路径解析见 `anima/config.py::seed_agents_dir()`（种子）与 `agent_dir()`（活体实例）。
