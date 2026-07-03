All load-bearing claims verified. Here is the blueprint.

---

# ANIMA 分层记忆实现蓝图 — Phase 3′（DISTRIBUTED_DESIGN v0.3 tiered memory）

> 本蓝图基于对 `store.py` / `pg_store.py` / `pg_db.py` / `decay.py` / `retriever.py` / `pg_sync.py` / `idle_scheduler.py` / `main.py` 的实读核对。凡标注 **【新建】** 的都是当前代码里不存在、必须新写的东西；其余均复用已存在的 API。

## 0. 现状锚点（已核对，作为一切改动的地基）

| 事实 | 位置 | 对分层的意义 |
|---|---|---|
| `create_memory_store()` 忽略参数，只 `return PgMemoryStore.create()` | `store.py:15-23` | 单一 logical store，改这里造双层 |
| `PgMemoryStore.create(db_path="", dsn="")`：传 `dsn` 就 `PgDatabaseManager(dsn=dsn)`，**无 local_dsn / 无 failover** | `pg_store.py:61-70` | 双 store 直接复用此单 DSN 路径，零新连接逻辑 |
| `PgDatabaseManager` 双 DSN 是**同一份数据的 failover**（primary→local），非双层；一连接一锁；`_ipv4_localhost` 规避 Windows ::1 卡顿 | `pg_db.py:30-71` | 两个 store = 两个 manager = 两连接两锁，local 必须 pin 127.0.0.1 |
| `_tag_origin` 把 `origin_node`+`locus` 塞进 `metadata_json`（仅 episodic） | `pg_store.py:52-59`；wiring `main.py:1186-1187` | 云端 promote 时必须让 cloud handle 也设了 origin |
| `save_memory_async` 在写路径上做 OpenAI embedding | `pg_store.py:103-116` | 本地 working 写应跳过 embedding，只在整理时嵌入 |
| `mark_consolidated` 用 `content_hash='consolidated:'||content_hash` 改写 | `pg_store.py:607-612` | **违反 DISTRIBUTED_DESIGN:189**（content_hash 是 G-Set 去重键），必须改 |
| `get_unconsolidated/below_threshold` 用 `content_hash NOT LIKE 'consolidated:%'` 过滤 | `pg_store.py:569-594` | 两套 marker 冲突，需统一 |
| `decay.py` 用 `?` 占位符 + `execute_many`，PG 层只认 `%s` → 生产环境 `update_all_scores`/`consolidate` **静默 no-op**（被 idle try/except 吞掉） | `decay.py:116-140,207-236`；`idle_scheduler.py:534,562` | 整理功能今天根本没在跑；这是 P3a 的先决 bug |
| `consolidate()` 选 `effective<0.1`（**最不重要**的），写 `type='knowledge'` 回同一 store，marker 用 `metadata_json.consolidated=True` | `decay.py:148-236` | policy 反了；目标要 promote **最显著**的 |
| retriever 只持有单 `self._store`；`_stage_recent` 读 `get_recent_memories(30)`、跳过 `metadata_json.consolidated`、`eff>=0.2` | `retriever.py:283-321` | recall 端读的是 `metadata_json.consolidated`（**不是** content_hash 前缀）→ 统一 marker 时以它为准 |
| `emotion_log` 在 `pg_sync._SPECS` 里双向复制；`get_latest_emotion` 取**全局最新一行、无 node 过滤** | `pg_sync.py:51`；`pg_store.py:264` | 跨节点情绪串味的根因 |
| `emotion_log` schema **无 node_id/origin 列**，`_tag_origin` 不覆盖它 | `pg_schema.sql:35-41` | 情绪 origin-tag 是**净新增工作** |
| 单一全局 `EmotionState`（`main.py:71`），`SessionState.emotion` 存在但主流程不用 | `session_manager.py:29` | per-locus 运行时多路是大重构，可延后 |
| `memory_deep_consolidation` HEAVY idle，3600s cooldown，budget-gated | `idle_scheduler.py:231,539-565` | 现成的低频整理触发器，直接复用做 promote |
| `DocumentStore(memory_store._db)` / `pg_sync(memory_store._db)` 直接吃 `._db` | `main.py:99,108` | 改成 composite 后必须保留/重定向 `._db`，否则崩 |

---

## 1. 架构落点

**结论：两个 store 对象，都是 `PgMemoryStore`，都跑 `pg_schema.sql`+pgvector；外面套一个 `TieredMemoryStore` 路由复合体【新建】。**

- **WORKING（本地、持久）** = `await PgMemoryStore.create(dsn=LOCAL_DATABASE_URL)` — 本地 Postgres。
- **LONG-TERM + PERSONA（云、共享）** = `await PgMemoryStore.create(dsn=DATABASE_URL)` — Neon。

**为什么是"两个 PgMemoryStore 对象"而不是别的：**
1. `create(dsn=)` 单 DSN 路径**已经存在**（`pg_store.py:62-70`），零新连接代码即可拿到两个隔离 store；且该路径**不设 local_dsn** → 两个 store 天然互不 failover、互不 sync，正好是分层要的隔离。
2. **不能用 SQLite**：SQLite/Chroma 后端已被删除（`store.py:3-6` 头注释），重新引入会丢掉 pgvector → 本地语义召回失效。本地用 Postgres 才能让 working 层继续 `search_memories_async` 的 `embedding <=> %s::vector`。
3. **不能复用现有 `_local_dsn` failover**：那是"同一份数据的热镜像"，`pg_sync` 会把 local 全量镜像 primary 并双向 reconcile（`pg_sync.py:_backup_primary_to_local`）——正是要消除的写争用。分层要的是"local 是独立的一层"。

**`TieredMemoryStore` 复合体【新建】**（放在 `store.py`，由 `create_memory_store()` 返回）：
- 持有 `.working`（本地）与 `.long_term`（云）两个 `PgMemoryStore`。
- 暴露 `._db` 作向后兼容：默认指向 `.working._db`（本地）。**必须处理两个耦合点**：
  - `main.py:99 DocumentStore(memory_store._db)`：RAG 文档若要跨节点共享，应显式改成 `long_term._db`；若只本地则留 working。**建议**：文档 RAG 归 CLOUD（`long_term._db`），与 persona 同属共享知识。
  - `main.py:108 PgSyncManager(memory_store._db)`：**停用**——不 `start()` 它（见 §5/§7 关于它对分层是错的）。
- 两个 store 都要在 `main.py:1186-1187` 处设 `origin_node`/`locus`（当前只设了一个 store）。

**DSN 配置**：`LOCAL_DATABASE_URL` 从 failover 副本改成"本地 working 真身"；`DATABASE_URL` = Neon long-term。二者语义正式分家（写进 `config/default.yaml:81` 注释）。

---

## 2. 写入路由（精确到 file:function）

**原则**：高频原始写 → **WORKING（本地）**；只有低频、经整理/版本化的东西 → **CLOUD**。没有任何 dual 写。

### → WORKING（本地，跳过 embedding）
| 调用点 | 写什么 | 改动 |
|---|---|---|
| `stages.py:94-97` EventRoutingStage `save_memory_async` type='chat'（用户消息） | episodic chat | 走 `store.working`；**去掉写路径 embedding**（改用 sync `save_memory` 或给 async 加 `embed=False`） |
| `response_handler.py:260-279` `_save_chat` type='chat'（助手回复） | episodic chat | 走 working；不 embed |
| `response_handler.py:181,212` `_handle_delegation`/`_handle_self_thought` type='observation' | episodic observation | 走 working |
| `response_handler.py:148-153` `log_emotion_async` | emotion_log | 走 working（见 §5） |
| `state_snapshots` / `llm_usage` / `audit_log` | 运维日志 | 走 working（本地即可，不必上云） |
| `env_catalog`/`env_scan_progress` | 每机缓存 | **已经**本地非同步（`pg_sync.py` 注释），无改动——这是"每节点表"的现成先例 |

**顺手修**：`audit_log` 每轮**写两次**（`response_handler.py:162` + `stages.py:375`），去掉其一。

### → CLOUD（低频、经手）
| 写入 | 来源 | 说明 |
|---|---|---|
| 整理产出的 `archive` 摘要行 | §3 consolidation | append-only、origin-tagged，**唯一**从节点流向云的 episodic 写 |
| `static_knowledge` upsert（`pg_store.py:381`） | 静态知识 | 版本化，`_reconcile_versioned` 合并 |
| persona/feelings/lorebook 文件、emotion baseline | §5/§6 | 罕写，version-vector/Eva-merge |

### 读改动（配合写路由）
- `get_session_conversation('local')`（`pg_store.py:191`）今天喂 prompt，**必须改读 working**——否则用户消息写在 local、prompt 却从 cloud 读会读空。`cognitive.py:197-227 load_conversation_from_db` 的 `get_session_conversation` 也改读 working。

---

## 3. 整理任务 consolidation（本地→云的单向桥）

### 3.1 先决：修 PG 静默 no-op（**P3a，最高优先**）
`decay.py` 的 `?`/`execute_many` 全部换掉。**推荐做法**：让 `decay.py` 不再直接摸 `store._db` 裸 SQL，改调 `pg_store` 已有的 PG-正确原语：
- `update_all_scores` → 用 `get_unconsolidated_memories` + `batch_update_decay_scores`（`pg_store.py:589,596`）。
- `consolidate` 的 fetch → 用 `get_memories_below_threshold`（→改为按显著性，见 3.3）。
这样一次性消灭 `?` vs `%s`，且不会再被 idle try/except 静默吞掉。

### 3.2 修 content_hash 改写 gotcha + 统一 marker
- **停止** `mark_consolidated` 改写 content_hash（违反 DISTRIBUTED_DESIGN:189，content_hash 是 soft-sync G-Set 的幂等去重键）。
- **统一到 `metadata_json.consolidated=True` 这一个 marker**——因为 recall 端读的就是它（`retriever.py:306`）。
- 改 `pg_store.mark_consolidated`（`:607`）：把 `content_hash` 改写换成 `SET metadata_json = jsonb_set(metadata_json,'{consolidated}','true')`。
- 改 `get_unconsolidated_memories`/`get_memories_below_threshold`（`:569,583`）过滤条件：`content_hash NOT LIKE 'consolidated:%'` → `(metadata_json->>'consolidated') IS DISTINCT FROM 'true'`。
- **合规性**：这个 marker 只写在 **本地 working store**、永不同步（working 层不上 sync），因此"in-place mutation 破坏 append-only G-Set"（DISTRIBUTED_DESIGN:193）不适用——分层恰好使这个 mutation 合法。这点要在代码注释里写清。

### 3.3 policy 反转：promote 显著的，不是衰减的
当前 `consolidate` 选 `effective<0.1`（最不重要的，是遗忘/压实策略）。分层要**相反**：
- **【新建】显著性闸门**，与 decay 闸门分离：`salience = importance * emotion.salience_multiplier`，或直接复用 `ImportanceScorer._detect_signals`（`importance.py:101`）的 question/instruction/name/emotion/tool_fail/evolution 信号。`salience >= SALIENCE_HIGH`（如 0.6）才 promote。
- 低价值记忆**留在本地**、随 decay 自然消失，永不上云。
- 诚实标注：`ImportanceScorer` 是纯正则手调、无学习，会误排；把它当"够用的显著性闸门"，别当真相。

### 3.4 promote 而非原地归档
改 `MemoryDecay.consolidate` 签名【改造】：`consolidate(local_store, cloud_store, llm_router, budget_ok, salience_threshold)`。
1. 从 **local** 取显著、未整理的行（`get_unconsolidated_memories` + 显著性过滤）。
2. `_cluster_by_topic`（`decay.py:251`）同类型 6h 窗口聚类（复用）。
3. `_summarise_cluster`（`decay.py:292`）：budget_ok→LLM tier2 150 字摘要；否则 rule-truncate（有损，已知）。
4. **写 CLOUD**：`cloud_store.archive_to_knowledge(summary, source_ids, metadata)`（`pg_store.py:618`）——append-only、`type='archive'`、经 `save_memory`→`_tag_origin` 自动带上 origin。**前提**：cloud_store 也设了 `origin_node/locus`（§1），否则 promote 出来的摘要无 tag。
5. **标 LOCAL 源已整理**：`local_store.mark_consolidated(source_ids)`（改造后写 metadata flag），纯本地、不同步。

### 3.5 触发器
- **复用** `memory_deep_consolidation`（HEAVY/deep idle，3600s，budget-gated，`idle_scheduler.py:231,539-565`）作为低频单向 promote 触发——这正是设计说的"N 个周期性整理器"。
- `_run_memory_consolidation`（`:539`）改为把 `local_store`、`cloud_store` 双传给新 `consolidate`。
- `memory_decay_update`（LIGHT，`:526`）保持**只本地**，不碰云。
- **【新建】flush-on-shutdown + on-reconnect**：节点关机前 / 重连时先 promote 一次积压（见 §7）。

### 3.6 去重
- 云端 append-only，`content_hash` 保持不变作幂等键（现在没被改写了）。
- 跨节点去重靠 `origin_node + id`（**不要**用 `_next_sync_seq`，它进程内计数、重启归零、非节点唯一——`pg_store.py:77`）。

---

## 4. 召回合并 read-merge（LOCAL working ∪ CLOUD long-term）

retriever 现在只有 `self._store`。改动集中在 `retriever.py` 这一个 choke point：
- **【新建】** retriever 构造增加 `long_term=` 参数（`main.py:308` 传 `store.long_term`）。
- `_stage_recent`（`:283`）保持读 **working**（自己近期的、未整理的原始行）。
- **【新建】** cloud 召回：新增一个 stage（或扩展 `_stage_semantic` `:337`）对 **long_term** 跑 `search_memories_async`（云端 pgvector）+ 取云端 `archive` 摘要。
- **合并**：沿用现有 RRF fusion（`_RRF_K=60`），把 working 与 cloud 的候选一起丢进 RRF 排名。
- **防重复计数**：一条本地原始行被 promote 后，本地已标 `consolidated`（`_stage_recent:306` 会跳过它），而云端只有它的**摘要**（不同 id）——所以不会同一内容既出原始又出摘要。额外保险：按 `id` 去重（`_find_candidate` 已存在 `:326`），并可用云摘要 metadata 的 `source_ids` 反查压掉仍在本地的同源行（跨节点 promote lag 期间的边角）。
- **离线**：cloud 查询包 try/except（`_stage_recent:291` 已是这个模式）→ 云不可达时静默降级为**只本地召回**。这是可接受的 tradeoff（跨节点召回有 lag）。

---

## 5. 情绪：per-locus 本地通道 + 慢速云 baseline

### 5.1 本地 per-locus 通道（working）
1. **从同步里摘除**：删掉 `pg_sync._SPECS` 里的 `emotion_log`（`pg_sync.py:51`）——原始情绪行不再双向复制，杜绝跨节点串味。
2. **加 origin 列（净新增）**：`emotion_log` 加 `node_id`（+可选 `locus`）列（`pg_schema.sql:35-41`），在 `log_emotion`/`log_emotion_async`（`pg_store.py:250-262`）从 `store.origin_node/locus` 盖章。注意：`_tag_origin` 只管 episodic，情绪 tag 是**全新**的。
3. **restore 过滤改动（本条即任务要求的 "restore filter change"）**：`get_latest_emotion`（`pg_store.py:264`）改 `WHERE node_id=%s [AND locus=%s] ORDER BY timestamp DESC LIMIT 1`；`restore_emotion_from_db`（`cognitive.py:181-195`）把 node_id 串进去 → 重启只恢复**本节点自己**的近期情绪，绝不吃到别节点的 mood。
4. **顺手修**：`log_emotion_async` 声明 async 却直接 blocking `write_sync`（`pg_store.py:252`）——转本地快路径时补 `asyncio.to_thread`。

### 5.2 慢速云 baseline（shared，罕写）— **全部新建**
- 今天没有共享 baseline：`_baseline` 只从静态 config 加载一次（`main.py:71`）、从不持久化。
- **【新建】** 共享 baseline 行走 `static_knowledge`（`category='emotion_baseline'`，per locus/persona），复用 `_reconcile_versioned`（`pg_sync.py:236`）做版本合并（非 append-only）。
- **【新建】baseline-fold job**（低频、单向，像 consolidation）：读本节点近期本地 `emotion_log`，算 4 维 EWMA 中心趋势，用版本合并折进云 baseline（数值 blend 或 Eva-LLM）。启动时用它 seed `_baseline`（替代 `main.py:71` 的 config）。
- 诚实：fold **必须低频单向**，若每次交互都写就重新引入了刚消除的争用。decay-rate 有不一致（`state.py` 文档 0.02 vs `heartbeat.py:301` config 0.05），fold 数学要选一个权威值。

### 5.3 per-locus 运行时（可延后）
真正把单一全局 `ctx.emotion` 拆成 locus-keyed 字典是大重构，牵动 heartbeat decay（`heartbeat.py:299`）、salience（`response_handler.py:272`）、tone（`:248`）。**建议延后**：5.1 的持久化+node 过滤已经在"存储层"实现了 per-locus 隔离；运行时多路作为独立后续。

---

## 6. 人格/灵魂：云端权威

- persona/feelings/lorebook/emotion-baseline 以 **CLOUD 为权威**（Neon）。
- prompt 编译时读（retriever Tier 0，`retriever.py:5`"Always load core identity + user profile + feelings"）。
- 编辑走 **version-vector / Eva-merge**（已定，即 `static_knowledge` 的 `_reconcile_versioned` + `_journal_lww` 冲突落盘，`pg_sync.py:62-83,236`）——罕写。
- **离线本地缓存**：把云端 persona/static_knowledge 定期拉一份到本地 working store 的对应表作只读缓存，云不可达时 prompt 仍能编译（见 §7）。

---

## 7. 离线与重连

- **在线要求最低**：working 是本地 PG（127.0.0.1，`pg_db._ipv4_localhost`），所有原始写恒成功；云不可达不阻塞任何交互路径。这是相对今天的**行为变更**（今天 local 只是同一份云数据的替身，`switch_to_primary` 会翻回去）。
- **本地 working 持久性 = 崩溃安全的唯一保障**：整理只在 deep idle 每 3600s 且 budget-gated 才跑，crash-before-consolidate 会丢 session——**除非本地原始写是持久的**。已核对：`PipelineCheckpointer` 每 stage 写 JSON 但 `check_incomplete` 重启时**丢弃不重放**（`checkpoint.py:66-77`），所以它**不是**持久点；**每轮的本地 episodic 写本身**才是持久点，必须同步落盘（working 已是真 PG，满足）。
- **重连时**【新建】：(a) 先跑一次 consolidation flush 把积压显著记忆 promote 上云；(b) 召回自然读到云端共享（含其它节点的整理产出，因为 cloud 是共享读源）。不需要把 pg_sync 那套双向 reconcile 拉回来。
- **停用旧 pg_sync**：它的 `_backup_primary_to_local` 全量下拉、`_replay_local_to_primary`+`switch_to_primary` 假设"一份数据 failover"，对分层全错；且它 `start()` 要求两 DSN 在**同一个 `_db`** 上（分层后不成立，会自动 no-op）。明确不 `start()`。
- **离线读缓存（可选）**：定期把云 long-term/persona 拉进本地只读缓存表，供离线召回；否则离线召回只剩本地 working。

---

## 8. 分阶段落地（每步独立可验，从最低风险起）

**验证床**：两个 docker Postgres——`LOCAL_DATABASE_URL`→本地 docker PG（working），`DATABASE_URL`→第二个 docker PG（扮"云"long-term）。都灌 `pg_schema.sql`+pgvector。**每一步改完必须真启动一次 app 验证**（记忆 feedback_verify_by_running：pytest 漏运行期接线 bug）。

### P3a — 修整理静默 no-op + 统一 marker（**单 store，零分层，最低风险**）
- 触碰：`decay.py`（`?`→复用 `pg_store` 原语）、`pg_store.py:569,583,607`（marker 统一到 `metadata_json.consolidated`，停止改写 content_hash）。
- 验证：单个 docker PG，灌几十条 episodic，手工触发 `_run_memory_consolidation`，看日志**真的**归档（今天是静默 0），且 `content_hash` 未被改写、`retriever._stage_recent` 正确跳过已整理行。
- 独立价值：即使不做后续分层，这也修好了一个生产静默 bug + DISTRIBUTED_DESIGN:189 合规。

### P3b — 双 store 脚手架 + recall union（写仍全落本地，云为空）
- 触碰：`store.py`（`TieredMemoryStore` 复合体 + `._db` 兼容 + DocumentStore/pg_sync 处理）、`main.py:70,99,108,308,1186-1187`（两 store、两 origin、停 pg_sync、retriever 传 long_term）、`retriever.py`（加 `long_term`、cloud 召回 stage、RRF 合并、离线 try/except）。
- 写路由（§2）：episodic/emotion/observation → working；`get_session_conversation`/`load_conversation_from_db` 读 working。
- 验证：app 启动、聊天正常；原始行只落**本地** docker PG；云 PG 仍空；召回 = 本地（union 云空 = 本地）。断开云 PG，聊天与召回不受影响。

### P3c — consolidation 变 promoter（本地→云、显著性闸门、flush-on-shutdown）✅ 已完成
- 触碰：`decay.py consolidate`（改成 promoter：`min_salience` 闸门、`local/cloud` 内部解析、非分层 `local is cloud` no-op、簇≥2 摘要/孤立逐字写云 `archive_to_knowledge`、云写在标 consolidated 之前）、`decay.py get_salient_unconsolidated[_async]`【新增于 pg_store：排除 archive+已整理】、`decay.py flush_promote`【新增 shutdown 用】、`idle_scheduler.py:539`（promotion 不再被 LLM budget 门控，budget 只决定摘要质量）、`main.py` shutdown flush hook（仅 tiered，best-effort）。
- 验证：双 docker PG（55432 working / 55433 cloud）跑 `verify_p3c.py`——4 条显著记忆促进（1 摘要 + 1 逐字，均 origin-tagged），低价值留本地，重跑 promote 0（不重复促进），非分层 no-op（0 归档）。
- **对抗式复审（workflow wf_3c459e53）抓到并已修 4 个 confirmed**：① MEDIUM 召回重复计数——Tier-2 语义搜索不过滤 consolidated → 本地原始 + 云副本双出（修：`retriever._is_consolidated` 过滤两层语义结果，与 Tier-3 recency 对齐）② MEDIUM embedder 停机时促进=永久丢失——被标 consolidated 的原始离开本地召回，而云副本 embedding 为 NULL 不可召回（修：促进前 `embedder.openai_available()` + 一次 `embed_openai` 探活，停机则整轮跳过；无 key 走 ILIKE 不受影响）③ LOW 并发双促进——idle 整理与 shutdown flush 并发读同批未标行写重复归档（修：模块级 `_PROMOTE_LOCK`，flush 见锁则跳过）④ LOW `local is cloud` 对象身份挡不住别名 DSN（localhost vs 127.0.0.1）（修：`store.py` 比较前折叠 host 别名）。复审后 `verify_p3c.py` 增补三项断言（召回不泄漏 consolidated 原始 / embedder 停机门 / 非分层 no-op）全过；89 单测无回归。

### P3d — 情绪 per-locus 本地 + node 过滤 ✅ 已完成（baseline fold = P3d-2 待做）
- 触碰：`pg_schema.sql`（emotion_log 加 `node_id TEXT` + 幂等 `ALTER ... ADD COLUMN IF NOT EXISTS`（迁移旧库）+ `(node_id,timestamp)` 索引）、`pg_store.py`（`log_emotion[_async]` 盖 `origin_node`；`get_latest_emotion` 有 node 时按 `WHERE node_id` 严格取本节点、取不到再回落全局最新）、`pg_sync.py:_SPECS`（**摘除 emotion_log**——情绪是 per-locus 信号，不进共享/failover 库以免串味；取舍：Neon failover 时单节点 mood 回基线，最佳努力、很快重积）。**cognitive.py 无需改**（store 内部按 origin_node 过滤；origin_node 在 `restore_emotion_from_db` 前已设）。
- 验证：docker working PG `verify_p3d.py`——node-A 只恢复自己 0.9（不串 node-B 更新的 0.1）、全局(未设)取最新、旧 NULL 行被严格过滤忽略/被全局拾取、迁移列生效。
- **对抗式复审（workflow wf_c318c4ea）抓+修 3 confirmed（同一根因）**：① MED 情绪 node key 取自**可选的 mesh** `net["node_identity"]`——mesh 关闭时（默认 secret 空→fail-closed）回落裸 hostname，跨启动翻转 + 非唯一 → 严格过滤命中 0 行 mood 回基线（违反 S2）。修：`main.py` 改从**持久化 `NodeIdentity()`**（node.json，mesh 无关，稳定唯一 `anima-<host>-<rand8>`）取 origin_node。② ③ LOW 严格过滤无空回退 → P3d 升级首启（旧行 node_id=NULL）/ 新节点首启 mood 回基线。修：`get_latest_emotion` 严格取不到时**回落全局最新**做连续性种子（稳态每节点有自己的行→永不触发→无串味）。复审后 `verify_p3d.py` 增补回退 + 稳态无串味断言，全过；33 单测无回归。
- **P3d-2 待做（baseline fold）**：慢共享情绪基线（跨节点缓慢趋同，止乒乓），与 per-locus 快通道分离。

### P3e — persona/soul 云权威 + 离线缓存 + 重连拉取
- 触碰：persona/static_knowledge 读走 long_term、离线本地缓存表、重连 pull。
- 验证：云在线时 prompt 用云端 persona；断云后用本地缓存仍能编译 prompt；重连后拉到其它节点的整理产出。

---

## 9. 失败模式与取舍（诚实清单）

| 风险 | 后果 | 处置/取舍 |
|---|---|---|
| **整理 lag** | 跨节点召回落后（≤3600s + budget-gate + 仅 deep idle） | 设计已接受；加 flush-on-shutdown/reconnect 缩短窗口 |
| **crash-before-consolidate** | 丢未整理 session | 本地 working 是真 PG、每轮同步落盘 = 持久点；`PipelineCheckpointer` **不**可靠（重启丢弃，`checkpoint.py:66-77`），别指望它 |
| **有损 promote** | rule-truncate 50 字 / LLM 150 字丢细节 | 设计已接受；靠显著性闸门保重要项，但 `ImportanceScorer` 纯正则手调、会误排——预期偶尔漏掉该留的 |
| **云不可达** | 跨节点召回降级为本地 | try/except 静默降级；本地写不受影响 |
| **与已建 origin-tagging 的冲突** | 云 handle 若没设 origin，promote 摘要无 tag | §1 强制两 store 都设 `origin_node/locus` |
| **与 mesh authz 的冲突** | promote 开始向共享云推 origin-tagged 行，恶意 peer 可伪造 origin（DISTRIBUTED_DESIGN:199） | sync/promote 传输层须把 `origin_node` 对认证过的 peer 校验；本蓝图不引入新传输，但 promote 上云这条路径需纳入现有 mesh authz |
| **与 boot-health 的冲突** | 多一个本地 PG 依赖，boot 自检要覆盖 | 启动自检加"本地 working PG 可连"检查；连不上应能回退（记忆 project_evolution_safety 的启动自检回退） |
| **`._db` 耦合** | DocumentStore/pg_sync 直吃 `._db`，复合体不兼容会崩 | `TieredMemoryStore._db` 指 working；文档 RAG 显式重定向到 long_term；pg_sync 不 start |
| **`_next_sync_seq` 重启归零、非节点唯一** | 不能当跨节点排序/去重键 | promote/去重一律用 `origin_node + id`，不碰 sync_seq |
| **两套 consolidated marker 不一致** | recall 读 metadata、pg_store 写 content_hash 前缀 | P3a 统一到 `metadata_json.consolidated`，且该 flag 只本地不同步 |
| **policy 反转陷阱** | 若只把旧 `effective<0.1` 选择指向云 = 把垃圾 promote、把重要的忘掉 | 必须新增独立高显著性闸门（§3.3），不是复用衰减闸门 |
| **情绪 origin-tag 是净新增** | 任务原述"已 origin-tag emotion_log"与代码不符 | schema 加列 + 盖章都是新工作，别当已存在 |

**净效果**：N 个并发原始写者 → N 个周期性整理器向共享云 append origin-tagged 摘要，三脑写争用溶解；云是共享存储底座、非认知主控（认知仍全分布、无 master）。召回 = 本地 working（自己近期）∪ 云 long-term（全节点整理产出）。

---

**关键文件清单（改动落点）**：`anima/memory/store.py`(复合体)、`anima/memory/pg_store.py:52,250-264,569-627`、`anima/memory/decay.py:99-245`、`anima/memory/retriever.py:283-357`、`anima/memory/pg_sync.py:51`、`anima/memory/pg_schema.sql:35-41`、`anima/core/idle_scheduler.py:526-565`、`anima/core/cognitive.py:181-227`、`anima/core/response_handler.py:148-279`、`anima/core/stages.py:94-97,375`、`anima/main.py:70,99,108,304-313,1186-1187`。
