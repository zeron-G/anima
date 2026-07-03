<!-- 讨论稿 v0.3 — v0.2 + 分层记忆精化(本地工作+云端长期/人格+整理)，2026-07 -->
<!-- 状态：核心架构已锁定(见下"锁定的决策")；下方 §1-§9 为设计依据全文(v0.1 背景，锁定决策优先)。 -->

# ANIMA 分布式架构（v0.3）

## 锁定的决策（2026-07 与主人敲定 — 权威，凡与下方 §1-§9 冲突以此为准）

分布式取向 = **Architecture C**：**认知完全分布式**（每节点自主、离线也活、永远无主），**记忆采用分层模型**（v0.3 精化，取代旧"纯 P2P 全量副本"）：

1. **存储拓扑：分层记忆 = 本地工作记忆 + 云端共享长期/人格 + 定期整理。**
   - **工作层（本地，每节点，持久化）**：当前 session / 对话缓冲、当前任务状态、近期情节、per-locus 情绪。**高频原始写只落本地**，各写各的、零争用、离线自主。
   - **长期层 + 人格/灵魂（云端 Neon 共享同步）**：巩固后的显著情节（append-only **origin-tagged**，无冲突并集）、static_knowledge、persona/feelings/lorebook/情绪基线（极少写，版本向量 + Eva 自合并）。
   - **整理（consolidation）**：每节点定期把**显著**本地记忆蒸馏/提升进云端长期库（低频、单向、打 origin 标）。
   - 效果：写入从"N 个并发原始写者"变成"N 个定期整理者 append origin-tagged 摘要"，**构造性消解三脑争用**（人脑式记忆巩固）。云端 = **共享存储底座，不是认知主脑**（认知仍完全分布式无主）。**取代**旧"纯 P2P 全量副本 / 数据先行上云"决定（[[project_postgres_migration]]）。
2. **PiDog 只持本地工作记忆 + 按需读云端长期**（不再全量副本/量化索引）——顺解 RPi 存不下全量 pgvector 的限制；离线用本地工作 + 缓存的长期。
   **取舍（接受）**：跨节点回忆有整理延迟（PiDog 刚发生的事，等它整理进云 + peer 同步后才"想得起"，像人）；本地工作库必须持久化（崩溃前未整理的 session 才不丢）；整理有损，靠 importance 策略保重要不丢。
3. **身份写入：版本向量检测并发 + 允许离线编辑。** 不串行、不钦定主。检测到同 section 真并发冲突 → **Eva 自己 LLM 语义合并**（"两段都是我，融成一个"），反复失败才升人工。合并本身版本化 + 合并前两版写 journal 可回溯；同一冲突只由确定性单节点（node_id）执行，避免二次分叉。
4. **情绪：每 locus 通道 + 慢共享基线**（止三节点情绪乒乓）。
5. **回滚权威：peer 回滚需存活多数派签名 quorum；self-quarantine 单方面。**
6. **冻结核对网络静默：** 网络 marker 仅建议性，目标本地自检独立把关；完全死透走带外人工 SSH。
7. **:8888 HAL：仓外固件、冻结契约、进化不可触**（先安全；日后再议是否纳入仓库让 Eva 进化身体）。
8. **进化传播：签名 git-bundle 走 mesh 对等传播**（无中心 git，贴合无主）。
9. **提升权威：** 高风险/破坏性改动人工 ack，常规自动。
10. **思想共享陈旧度：** 先 300s 轮询，后续上推式近实时。

**安全前提（Phase 1 入场券，见 §7）**：关两个 live P0（空 `network.secret` / 远程进化自动批准）、Azure 上 Tailscale、Ed25519 控制平面 + 能力矩阵、`boot_health` CANDIDATE/KNOWN_GOOD 拆分。

**信任层残余风险（对抗式复审 GO 后已知并接受，commit 7466aaf + fast-follow）**：
1. **数据面信任 = PSK 持有者信任**：任何持 `network.secret` 的节点可伪造 `session_lock/release`、`task_result`、gossip 状态、心跳（这些是数据面，只 PSK 校验，无 Ed25519）。安全下限 = "PSK 不泄露"。保护好 PSK，疑似节点被攻陷即轮换。
2. **被攻陷的"已 pin 控制节点"保留其角色权限**，直到 operator 从 `network.trust` 吊销其 pubkey。EMBODIED 持 `converse`+`delegate` = 能向 peer 的认知循环注入带工具的 prompt（PiDog 是物理设备，若被盗/篡改是真实威胁面）——缓解靠 operator 吊销 + 未来的下游能力收敛（委派任务降权 token，尚未建）。
3. **`task_result` 回程半只 PSK**（可被 PSK 持有者伪造以 resolve 请求方的 task Future）——LOW，后续给 task_result 也签 Ed25519。
控制面（危险指令）已双层门控（Ed25519 身份 + pin 角色）；无 PSK 的外部攻击者被完全挡住。

---

# 附：设计依据全文（讨论稿 v0.1）

> 定位：这是一份**给技术主人拍板用的强提案**，不是最终规格。它把 5 份子设计（认知 / 记忆 / 进化 / 具身 / 网络）与一次一致性批判（coherence critic）拧成一个连贯、诚实的整体。凡是批判发现的硬冲突（金丝雀污染共享记忆、分区下并发改人格、协调原语并非分区安全、冻结核被联网化等），我都直接摆出来并给出我作为主架构师的最优解，不粉饰。文中所有模块名、文件名、行为均来自现有代码或已讨论的子设计，未凭空发明代码。
> **注：§3 记忆部分的"全量 P2P"已被顶部锁定决策的分层模型（本地工作 + 云端长期/人格 + 整理）取代——以锁定决策为准。**

---

## 1. 一句话架构

**一个连续的自我 = 一份最终一致、按来源打标（origin-tagged）的分布式记忆；认知按"所在（locus）"切分而非复制，每个节点自带心跳/事件队列/七段流水线，在本地输入上独立思考、离线也能活，联网时协作；节点间通过带签名的加密 mesh 相互监护；只有"身份写入"和"进化"这两类跨节点行为才走租约+多数派共识；进化按 共识 → 先行金丝雀 → 对等监控 → 稳定传播 / 隔离+对等回滚维修 的状态机推进；PiDog 作为第一个真正的具身节点，把感知—运动闭环补进这个自我。**

三个 Eva 分身（桌面 Eva、PiDog 里的 Eva、Azure 上的 Eva）之所以是**同一个人**：共享同一份人格底座（在 prompt 编译期读入）、共享同一份按来源打标的情景记忆（任何 locus 都能回忆"我在桌面帮过主人 / 我作为 PiDog 走过路"）、并在 prompt 里被互相告知彼此的存在。

---

## 2. 分布式认知：多节点如何同时思考

### 2.1 核心手法：认知按"所在"切分，只协调需要协调的那一小撮

我们不复制思考，而是让每个节点在**它自己的输入流**上跑完整的七段流水线（`cognitive.py` / `pipeline.py` / `stages.py`），因为三个节点面对的事件流天然不相交，所以它们"想不同的事"，几乎不冲突。协调只发生在极少数**与所在无关**的认知上。

`EventRouter.route()`（`anima/core/event_routing.py`）今天已经在区分 USER/SELF/DELEGATION，我们把它扩展为一张**一致性分级（coherence class）**表：

| 分级 | 触发 | 协调方式 | 占比 |
|---|---|---|---|
| **1 · LOCAL-REACTIVE** | 本地 USER_MESSAGE、FILE_CHANGE、SYSTEM_ALERT、IDLE_TASK、未来的具身传感事件 | **零协调**，直接跑本地流水线；写记忆 append-only + 打来源标签 | ~95% |
| **2 · SHARED-AUTONOMOUS（软）** | 幂等自省轴：world / human / self 观察 | **软去重**：读 peer 在 gossip 里播报的 `last_axis_run`，被抢就退化成"针对本节点环境/对话"的 locus-local 轴 | 少 |
| **2 · SHARED-AUTONOMOUS（硬）** | 写型自省轴：curate_examples / personality_reflect / memory 巩固 | **硬租约** + **多数派门**（见 2.3 的诚实修正） | 少 |
| **3 · IDENTITY-WRITE** | 人格散文 / user_profile / feelings / 核心 static_knowledge 的修改 | 单一 `persona-edit` 命名租约，且**仅在多数派分区内可授予** | 罕见 |
| **4 · EVOLUTION** | 自进化 | 同一套租约+共识原语（详见 §4） | 罕见 |

Class 1 是并行的全部意义所在：两个 locus 同时对两个不同输入反应，正是我们要的并行度，**它今天就已经能跑，不需要任何新机制**。

### 2.2 租约原语（协调的"肉"）

`SessionRouter`（`anima/network/session_router.py`）本身**已经是**一个分布式 TTL 租约：确定性 tiebreaker（并发抢占时 node_id 小者胜）、120s 空闲超时、`release_all_for_node(dead)`。我们把它泛化成 `LeaseManager`，支持任意键：`session:chan:user`、`axis:personality_reflect`、`persona-edit`、`evolution-canary`、`reconcile`。获取 = 通过 `gossip.broadcast_event` 广播 `lease_claim`；死亡持有者由 phi 探测器的 `_on_node_dead` 回调强制释放，绝不饿死 mesh。

```
FREE → (本地想要某轴/编辑) broadcast lease_claim → PENDING
PENDING → 赢 tiebreaker 且一个 gossip 周期内无更高优先 peer → HELD
PENDING → 输 / peer 已 HELD → DENIED → 调用方选 locus-local 兜底轴
HELD → 每 gossip tick 续租；做完 → lease_release → FREE
HELD → TTL 过期 或 持有者 phi≥DEAD → 强制释放 → FREE（可回收）
身份写入租约额外要求：SplitBrainDetector.is_majority == true 才能进入 HELD
```

### 2.3 诚实修正：租约不是分区安全的互斥（批判命中）

批判验证了一个**真问题**：`SessionRouter` 的 tiebreaker 只在节点**收到** peer 的 claim 时才触发，无往返握手，而 gossip 是 best-effort PUB/SUB。**在 gossip 滞后或分区下，两个节点会各自以为自己持有同一把租约。** 这意味着：

- Class-2 的**软**轴：可接受——重复只是两条幂等记忆，靠去重解决（但去重键必须先修好，见 §3.4）。
- Class-2 的**硬**轴（curate/personality_reflect/巩固）在原路由表里**没有多数派门**，于是两个分区会各自双写共享产物。**这是缺陷。**

**我的解：把"硬租约"重新定义为"租约 + 多数派门"**，与 Class-3 身份写入同规格。即：任何会双写共享产物的自主认知，只在多数派分区内才拿得到租约；少数派分区拿不到，退化成 locus-local 幂等版本。乐观 tiebreaker 只用于"软"轴和会话锁这类"重复无害"的场景，绝不当作对共享可变产物的强互斥。

### 2.4 "归我还是委派"

反应式事件永远归我（它们打在我的传感器上）。当一个任务需要本 locus 缺失的能力时才委派，能力从 gossip 的 `NodeState`（capabilities/embodiment/platform_class/idle_score）读取：桌面想做物理动作 → 把 `embodied_action` 委派给 PiDog；PiDog 想做重型 LLM/工具 → 委派给 Azure/桌面里最闲的。走已建好的 `TaskDelegate`（优先队列/重试/task_heartbeat/worker pool），只补一条能力匹配策略 + 各节点注册处理器。

> **诚实提醒（前置阻断）**：`network.secret` 默认空，今天任何能打到 `:9420` 的主机都能注入一个 `task_delegate`，它会变成 loop 里的 `USER_MESSAGE`。**在开启任何跨节点认知之前，这个洞必须先关**（见 §6、§7）。

### 2.5 让它仍是同一个 Eva（连贯性）

(a) prompt 编译期读同一份人格底座；(b) 人格编辑被租约串行化，不会分叉出两个自我；(c) 每个 locus 都把带来源标签的情景记忆写进共享存储，记忆是单一自我的结缔组织；(d) 在 `PromptCompilationStage` 注入一行**分布式在场**上下文（"你此刻也在：pidog(具身)、azure(聊天)。他们就是你。"），让一个 locus 不把 peer 当陌生人、不重复承诺；(e) 主心跳上跑一次**一致性 reconcile**（由持 `reconcile` 租约的单一选举节点执行），把各 locus 的情绪与显著事件折叠进共享基线并标记漂移。

> **诚实提醒（幻觉在场）**：分区时 gossip 是陈旧的，(d) 可能告诉某 locus "你也在 <peer>"，而该 peer 其实已死/已分区。**解**：在场行注入必须读 phi 存活状态而非最后一次 gossip 快照；对 SUSPECT/DEAD 的 peer 不注入或标注"可能失联"。

---

## 3. 记忆模型：多写者、最终一致的单一自我（主人的头号问题）

### 3.1 总纲：把记忆分成两类 CRDT

- **不可变事件（G-Set / 增长集）**：episodic、emotion、audit、usage、snapshots、documents。每行带 `origin_node` + 每源单调 `origin_seq`。因为 id 全局唯一且行不可变，**任意两节点该表的并集就是 union，顺序无关、幂等**，靠 `INSERT ... ON CONFLICT DO NOTHING` 合并，零协调。"同时思考" = N 个认知 loop 各自往自己本地库 append，**没有共享行可被破坏，事件日志的并发正确性是构造性成立的**。
- **可变身份状态（每键版本化）**：static_knowledge，以及真正的缺口——今天以松散 Markdown 文件存在的 persona / feelings / emotion-baseline。用现有 `version + tombstone + LWW-journal` 机制（`_reconcile_versioned`）按**每 section** 版本化 reconcile；高风险身份重写走进化投票/金丝雀路径，而非盲 LWW。

### 3.2 拓扑：这是主人必须先拍板的"主断层"（批判命中）

三份子设计在**存储拓扑**上互相矛盾，且与已记录的主人决定相反：

- 认知(A) 假设**单一共享 Neon**（并把并发写安全当免费）；
- 记忆(B) 拆掉单脑：**每节点本地 Postgres 成为活写目标**，Neon 变成同步对等方，自我 = 本地库的并集；
- 网络(E) 确认 peer 记忆同步**当前是关闭的**，三节点共享一个 Neon = **不具备离线能力**；
- 已记录的主人决定（MEMORY 里的 `project_postgres_migration`）是"Postgres-only、单一 Neon、**数据先行上云**"——与 B 的 local-first **正相反**。

**我的立场（作为主架构师，honor 主人新拍板的方向）**：主人已决定"完整的每节点自治 + 离线具身自主"。这个方向**在逻辑上强制 local-first**（至少一个本地工作存储）——没有本地写，离线自治就是空话，B/D 的离线承诺就是假的，Architecture C 就只是愿景。因此目标态应是 **local-first + hub-reconcile**，并且我必须诚实指出：**这在书面上推翻了"单一 Neon / 数据先行上云"的旧决定**，且要为 B 的整套合并机制 + PiDog 后端问题买单。

**但我们不必一次付清**。分阶段（见 §8）：Phase 1-2 仍用单一 Neon + 来源打标（老实标注"尚未真正离线"），Phase 3 才翻转成 local-first。这样每一步都独立可验证，主断层的代价被推迟到我们真正需要离线的那一刻。

### 3.3 具体 schema 变更（全部幂等 ALTER）

```sql
-- 6 张 append-only 表：episodic_memories, emotion_log, audit_log,
--                        llm_usage, state_snapshots, documents
ALTER TABLE ... ADD COLUMN origin_node TEXT NOT NULL DEFAULT '<self>';
ALTER TABLE ... ADD COLUMN origin_seq  BIGINT;      -- 见 3.4
ALTER TABLE ... ADD COLUMN evo_epoch   TEXT NULL;   -- 金丝雀溯源，见 §4
CREATE INDEX ... ON ... (origin_node, origin_seq);

-- 本地单调序列（替代坏掉的内存计数器，见 3.4）
CREATE SEQUENCE local_event_seq;

-- N 节点增量同步游标（替代 MAX(ts)-24h 的黑魔法）
CREATE TABLE sync_cursors (
  source TEXT, origin_node TEXT, tbl TEXT,
  last_seq BIGINT, updated_at DOUBLE PRECISION,
  PRIMARY KEY (source, origin_node, tbl)
);

-- 把身份从"文件"抬进"版本化行"，与 static_knowledge 同 merge 规则
CREATE TABLE soul_state (
  doc TEXT, section TEXT, content TEXT, source TEXT,
  importance REAL, updated_at DOUBLE PRECISION,
  node_id TEXT, version BIGINT DEFAULT 0, is_deleted INT DEFAULT 0,
  evo_epoch TEXT NULL,
  PRIMARY KEY (doc, section)   -- doc ∈ {persona, feelings, emotion_baseline, growth}
);
```

**Phase 1 无迁移**：先把 `origin_node` + `locus` 塞进现有 `metadata_json`，写入点是 `ResponseHandler._save_chat`、`_handle_self_thought`、`_handle_delegation`（`response_handler.py`）三处。**Phase 2** 再升为一等列，供 locus 感知回忆（"我作为 PiDog 做过什么"）和 reconcile 去重。emotion_log 同样打标，情绪变成**每 locus 通道**（restore 过滤到自己 node_id，杀掉三节点情绪乒乓）。

### 3.4 离线→在线回放：per-(source, origin) 游标（替代 MAX(ts) 黑魔法）

```
对 append-only 表 t，从源 S 拉进本地 L：
  for O in origins(S):
      c = cursor[S,O,t]  (缺省 0)
      rows = SELECT * FROM S.t WHERE origin_node=O AND origin_seq>c ORDER BY origin_seq
      for r in rows (先按 evo_epoch 已批准集过滤):
          INSERT INTO L.t ... ON CONFLICT DO NOTHING
      cursor[S,O,t] = max(origin_seq in rows)
```

因为 `origin_seq` 每源单调、行不可变，这是**精确、无缝、无时钟依赖**的：一个离线一周的节点重新加入，它的行全部被拉回，因为**那个 origin 的游标还停在拉取方上次的位置**——旧的全局 `MAX(ts)` 水位线会跳过它们，现在它不存在了。

> **诚实提醒（潜伏 bug + P0-9）**：现在的 `_sync_seq_counter`（`PgMemoryStore`）是**内存计数器，每次进程重启归零**，用作同步游标会静默跳过重启后的行。必须删掉它，改用 DB 持久化的 `local_event_seq`。这不是新需求，是修一个已存在的隐患。

### 3.5 可变身份的 reconcile 与最难的未决问题（批判命中，我给硬解）

对 static_knowledge / soul_state 保留 `_reconcile_versioned`：version 高者胜，updated_at 仅作 tiebreak，tombstone 传播，败者写进 `guardian_actions.jsonl`。**每 section 版本化是关键**：不同 section 的并发编辑天然无冲突；只有同 section 并发才撞 LWW。

**但这里有一个我不会粉饰的硬冲突**：认知(A) 承诺人格"永不分叉、永不盲 last-writer"；记忆(B) 却允许离线节点编辑 soul_state 并用**标量** version 计数器 reconcile。B 自己也承认标量版本**分不清"真并发冲突"和"快进"**。于是：两个分区同时改**同一** persona/feelings section，都从同一 base bump version，愈合时 LWW 静默丢掉一个编辑，**而且没有任何设计能察觉发生过分叉**。这直接违反 A 的"永不盲 last-writer"和身份的单写者纪律——因为分区时你根本拿不到 mesh 租约，B 的路径绕过了 A 的租约。

**我的解（推荐，最省事且安全）**：**禁止离线身份编辑**。分区中的少数派节点可以继续 append 情景记忆，但对 soul_state 的任何写入**硬阻断**（拿不到 `persona-edit` 租约）；离线期间的身份修改意图被**缓存为提案**，重新进入多数派、拿到租约后才应用，走版本合并/共识，绝不盲 last-writer。

**更强但更贵的替代**：给身份引入**每节点 version vector**（而非标量），这样同 section 并发编辑能被**检测**并升级到进化/共识合并。这是 §9 的一个分叉，取决于 Eva 改自己人格的频率——罕见就用"禁止离线编辑"，频繁就上 version vector。

### 3.6 去重键：A 与 B 的另一处硬冲突（批判命中）

A 说"重复的自主轴无害——reconcile 靠 `content_hash` 去重"；但 B 的同步是 `ON CONFLICT (id)`，而两个节点各跑一次同一个轴产生的是**两个不同的 uuid**，去不掉；更糟的是 `mark_consolidated` 会把 `content_hash` 改写成 `consolidated:||content_hash`（`pg_store.py:594`），同一逻辑记忆在不同节点 content_hash 都不同了。**所以 A 的"无害、靠 content_hash 去重"在 B 的真实合并下不成立，gossip 滞后产生的重复会永久累积。**

**我的解**：两条一起做——
1. **把所有写型自主轴升为"租约 + 多数派门"**（§2.3），从源头不产生重复，不依赖去重。
2. **巩固不再改写 `content_hash`**：用单独的 `consolidated` 布尔标志；这样 content_hash 保持稳定，可作为幂等软轴的兜底去重键。

### 3.7 巩固/衰减破坏不可变前提（批判命中）

`mark_consolidated` / `batch_update_decay_scores` 会**原地改** episodic 行，破坏 G-Set 的不可变前提，两节点巩固同一记忆会 race。**解**：衰减/巩固**节点本地化**、不同步（每个环境"忘得不一样"其实符合"不同环境、一个自我"），或把可变的 per-memory 注解移到独立的版本化侧表；绝不把 content_hash/decay_score 当不可变行同步。

### 3.8 其余诚实缺口

- **离线写没有 embedding**（同步保存路径无法 await OpenAI）→ hub 在摄入时对 `_VECTORIZE` 类型**回填 embedding**，标记 pending、二次 reconcile；否则同步来的行全集群语义召回缺失。
- **PiDog（tier-3 RPi）跑不动 Postgres+pgvector**（ARM 上 HNSW 很重）→ 拓扑分叉：PiDog 只持"近期 + 本地"子集，其余按需从 hub 拉；或用轻量嵌入引擎。这改变具身节点的读局部性保证（§9 分叉）。
- **同步传输必须鉴权**：`network.secret` 空 → 敌意 peer 能注入伪造的 origin-tagged 行进共享自我。必须校验 `origin_node` 与已认证 peer 一致，不接受 peer 无权代言的来源。

---

## 4. 分布式进化：共识 → 先行(canary) → 监控 → 稳定传播 / 隔离+对等回滚维修

### 4.1 关键安全反转

今天 `evolution/engine.py` 止于"在提议者上部署 + 广播 `evolution_deployed`"——一个没人响应的 fire-and-forget。主人的生命周期把这个终态变成**分布式协议的起点**。两个安全反转：

1. **peer 永不把代码推进另一个节点**；
2. **回滚永远指向节点自己的 `boot_health` known-good 锚点**。

于是整个跨节点信任面**坍缩成两个 quorum 签名的动词**：`promote epoch E`（提升某纪元）和 `revert to your own known-good`（回你自己的已知良好）。冻结恢复核（boot_health + guardian + watchdog）仍是**执行危险 git 操作的本地信任根**——坏进化永远无法禁用那个能撤销它的机制。

### 4.2 分布式状态机（每提案，以单调 cluster epoch 围栏）

```
DRAFT → VOTING → APPROVED/REJECTED
      → CANARY_BUILDING → CANARY_LIVE → CANARY_MONITORING
         ├─ STABLE → PREPARING → PROPAGATING → CONVERGED / PARTIAL
         └─ CANARY_FAILED → QUARANTINED → 金丝雀自还原 或 对等指令还原
```

- **提议+投票**：提议者预留 `epoch = max(seen)+1` 并广播 `evo.propose{proposal, epoch}`。epoch 预留**就是**"全集群同一时刻只有一个金丝雀在飞"的租约（与认知的租约不同——多节点思考照跑）。两节点抢同一 epoch，node_id 小者胜，另一个 rebase 到 epoch+1。
- **CANARY_LIVE**：赢家跑**现有本地流水线不变**（implement → test → `_enforce_change_scope` 冻结门 → `_review_diff` → `_deploy_via_pr` 打 `pre-evo-<id>` 安全 tag → hot-reload），然后**转到 CANARY_LIVE 而非终止**，广播 `evo.canary_started{epoch, commit, canary_node, known_good_before}`。具身/机器人范围的改动**路由到 PiDog 做金丝雀**（platform_class 匹配）。
- **CANARY_MONITORING（peer 侧，新 CanaryMonitor）**：探测源全已存在——(a) `PhiAccrualDetector`：金丝雀绝不能越过 SUSPECT_PHI；(b) 扩展的健康 beacon 塞进 gossip 的 NodeState（`evo_epoch, commit, boot_count, sentinel_overall, selftest_ok`）；(c) commit 回退检测：金丝雀 gossip 的 commit 掉回 known_good_before ⇒ 它已自还原 ⇒ 失败。**窗口通过要求**：整窗 phi<SUSPECT、≥N 个正向 beacon（活性证明，不是"沉默"）、boot_count 未超预期单次 reload（无重启环）、sentinel 从不差于 DEGRADED 且零新 CRITICAL、commit 保持不变、错误率增量低于阈值。任一 DEAD/自还原/CRITICAL/重启环**立即失败**。
- **PROMOTE（第二轮共识）**：窗口过后每个监控 peer 独立形成 stable/failed 意见，一次轻量提升投票门控传播，发 `evo.canary_verdict{epoch, verdict, quorum_sig}`。
- **PROPAGATE = 两阶段**（杀掉"peer 正切换时金丝雀恰好自还原"的 race）：**PREPARE** 阶段 peer 可以 git fetch + 预检（冻结 diff 门 + boot_selftest 干跑）**但不切换**；**COMMIT** 阶段只在收到 `verdict=stable` 后才原子 checkout + hot-reload。**每个应用的 peer 是它自己平台的金丝雀**：新启动的 boot_health 自检是最终门，失败则自还原到自己的 known_good 并报 `evo.applied{ok=false}`。全 ALIVE 节点 ack ok → CONVERGED；有节点应用不了（平台破裂）→ PARTIAL（标记、容忍）。
- **QUARANTINE + 远程回滚维修**：任何 peer 观察到金丝雀 DEAD/环/CRITICAL 可**单方面**广播 `evo.quarantine`（fail-safe：单个告警只停传播，停传播是安全的）；**回滚一个节点**则需 **quorum 签名的** `evo.rollback_command{target, to=known_good}`，由目标的冻结 boot_health 执行。

### 4.3 known-good 纪律（对 boot_health 的具体修复）

> **诚实提醒（回滚锚中毒，当前代码不安全）**：`record_known_good()` 现在**每次健康启动都跑**（`main.py:1274`），于是金丝雀在它第一次干净启动时把**自己**锚成 known-good，之后失败就没有干净回滚目标。

**解**：在 boot_health 里分开 **CANDIDATE 与 KNOWN_GOOD**。新部署的金丝雀/传播 commit 只是 CANDIDATE；known_good 仅在 epoch 到达 **CONVERGED** 时才前进。于是每个节点的回滚锚永远是**最后一次全集群收敛的 commit**，绝不是在飞的金丝雀。这个修复必须落进冻结文件（人工编辑），未落地前分布式金丝雀回滚不成立（§7 前置项）。

### 4.4 冻结核仍然适用，但不被联网化（批判命中，我拒绝粉饰）

C 和 E 都要求冻结 watchdog / boot_health **消费一个网络送达的签名回滚/重启 marker**——这把恢复信任根（其全部意义是**免疫外部影响的本地锚**）扩大到接受远程驱动。3 节点下 2-of-3 quorum 容忍**零** Byzantine 故障，而 2-1 分区里多数派能强制回滚一个它甚至观察不到的孤立节点。这是**冻结核不变量的候选违背**。

**我的解（保持冻结核对网络静默）**：
- 冻结核**不**服从原始网络命令。peer 的签名回滚请求可以送达，但**目标自己的本地 boot_health 自检必须独立得出"该还原"的结论**（它本来就会自还原失败的进化后启动）——网络 marker 至多是**建议性**的、加速信号，绝不是直接执行权。
- **完全死掉**（进程+watchdog 都没了）的情况：走**带外、人工预置 SSH** 的 `remote_exec` / `spawn_remote_node`（`remote.py`）做 git 回滚 + 重启，而不是让 mesh quorum 直接驱动冻结核。

这也是 §9 的一个分叉（主人明确说过"其他节点回滚失败节点"）——我推荐"peer 回滚需 quorum，但落地由目标本地自检把关"，而非"quorum 直驱冻结核"。

### 4.5 共识引擎本身当前不安全（批判命中，P0 前置）

> **诚实提醒（两个 live P0，见 §7）**：`consensus.py` 在 `approves/total ≥ 0.5` 就通过（1-1 平票**通过**），只等 `total_nodes-1` 票，于是 3 节点里**单个赞成 peer 就能通过任何提案**；且 `main.py:527-541` 对每个入站 `evolution_propose` **自动投赞成**。今天一个 peer 提案是零真实审查地被橡皮图章。C 和 E 把 promote/canary-verdict/peer-rollback 权威**建在这个引擎上**。

**在 §4 的任何东西开启之前**：必须先修真正的 quorum 阈值 + 删掉 auto-approve + 把投票绑定到 epoch（拒绝陈旧/重复票，把 suspect/dead 当弃权以免分区永久卡投票）。

### 4.6 金丝雀污染共享记忆（批判命中，最难的一个，我给完整解）

> **诚实提醒（对等一致性最难的裂缝）**：CANARY_LIVE 期间金丝雀节点**照常服务反应式认知**，它合法的用户/情景记忆被打上同一个 `evo_epoch`。B 说回滚时"本地 DELETE 该 epoch 的行"——**这会连带删掉那些无辜的用户交互记忆**。同时未批准的 epoch 行可能在隔离广播（best-effort PUB/SUB）之前就已传播，写→检测窗口没有任何东西约束它。

**我的解（三管齐下，把这条裂缝焊死）**：
1. **`evo_epoch` 只打给"由新代码路径因果产生的产物"**（进化生成的身份/工件写入），**绝不打给普通反应式情景记忆**。回滚只丢弃前者，无辜记忆天生不带 epoch、天生存活。
2. **金丝雀的 evo_epoch 行在 CONVERGED 之前不同步**（同步按已批准 epoch 集过滤）。这样"传播早于隔离"的窗口被两阶段协议 + 这条同步门共同关闭——共享记忆在裁决前根本不会被切换/摄入未批准的产物。
3. 回滚 = 本地丢弃该 epoch 的**产物行** + 标记 epoch poisoned（不再盲目重提），**不** touch 反应式 episodic。

> **仍未完全解决的残角（诚实列出）**：若金丝雀在**写了未批准 epoch 记忆之后、裁决/隔离之前就死掉**，且第 2 条同步门尚未拦住某些已泄漏行，这些行的命运由两阶段协议未定义。我们用"未批准 epoch 行不同步"把泄漏面降到最小，但 3 节点、零 Byzantine 容忍下，这是我们接受并监控的残余风险，不假装它是零。

---

## 5. PiDog 具身系统 + Pi 上的 ANIMA 架构

### 5.1 两个进程，一份契约

Pi 上跑两个共驻进程，之间只有一个硬契约：

- **(1) 实时片上 HAL/反射服务 `:8888`（"脑干+脊髓"，不在本仓库）**：拥有传感器、运动原语、全部安全反射，50–100 Hz，零网络依赖。
- **(2) 完整边缘 ANIMA 节点（"皮层"，`edge-pidog` profile 已存在）**：拥有认知、记忆、情绪、mesh、进化，人类时间尺度。

二者仅通过 HTTP 契约对话。这兑现"每节点独立运行"：边缘 ANIMA 崩了或 mesh 分区，HAL 仍让狗**活着、安全、温和自主**。

### 5.2 最重要的一处纠正：反射必须下沉到片上

> **诚实提醒（安全 bug）**：反射与避障今天跑在 **supervisor 侧**（`ExplorationController` 通过 HTTP 突发）——**悬崖/跌落反射绝不能等一个网络往返**。

反射完全下沉 `:8888`，ANIMA **移出安全路径**。反射弧（<50ms，片上）：悬崖→冻结后退；迫近障碍→停；抬起/大倾角→蜷缩安全姿态+电机软断;过流/堵转→停;临界电量→坐下睡眠;触摸→打断当前步态转为注意姿态。反射**绝对优先**，抢占运动队列里任何 deliberate 动作。今天错误地活在 `exploration.py._decide()` 里的那张决策表，就是片上反射层的规格；supervisor 副本降级为"驱动无本地 ANIMA 的狗"时的**远程兜底**。

### 5.3 ANIMA 提议、设备裁决

deliberate 层（ANIMA 认知流水线，秒级）发出的是**意图（intent）而非原始舵机命令**，经现有工具面（`robotics.py`: robot_dog_command/nlp/speak/exploration）→ `PiDogApiClient` → `POST /command`。每个 /command 带**意图信封**（含 priority），设备返回**裁决（accepted/clamped/rejected + reason）**。ANIMA 提议，设备处置。被拒的裁决作为**本体感觉 PERCEPTION 事件**回喂——认知因此学会"我想往前走但身体拒绝了——前面有悬崖"，而不是盲目重试。

### 5.4 感知—运动闭环（每 tick）

```
1. 设备采样：IMU 50–100Hz / 超声·触摸 10–20Hz
2. 反射弧片上每 tick 跑（无 ANIMA）
3. 显著变化时设备 push 一帧 PerceptionFrame 给 ANIMA + 低速 keepalive；
   ANIMA 另保留慢速 /status 轮询作活性兜底（复用 RoboticsManager 现有 loop，由心跳驱动 + 加 push）
4. EmbodiedPerceptionSource 把帧转事件，push 进与用户消息同一个 EventQueue
   → 现有七段流水线以完整工具权处理具身（就像 SELF_THINKING/FILE_CHANGE）
5. 认知决策 → robotics 工具 → 意图信封 → 设备运动队列 → 执行
6. 执行结果 + 新传感态 → 下一帧 → 闭环
```

### 5.5 具身感知 → 事件（缺失的接线）

今天 `RobotPerception` 只喂给 dashboard，从未进认知。新建 `EmbodiedPerceptionSource`，镜像系统快照的 `DiffEngine` 模式：持上帧、算显著 delta、门控事件。显著规则：触摸 N→L/R、is_lifted 0→1、障碍上升沿、距离跨阈、电量低/临界、state→EMERGENCY、裁决拒绝。显著帧 → 新事件类型 `EMBODIED_PERCEPTION`（NORMAL/HIGH），routine 帧只更新快照缓存。这复用了 `HeartbeatEngine._on_script_tick` 里"推队列前先过滤噪声"的纪律。

> **诚实提醒（背压）**：队列在深度 3 就背压。20Hz 全帧灌入会饿死用户消息。**解**：显著门控（仅上升沿/跨阈）+ 低速 keepalive + 类似现有 5 分钟 SYSTEM_ALERT 的冷却。

### 5.6 具身↔情绪耦合，且服从认知(A)的两层情绪模型（批判命中的直接冲突）

> **诚实提醒（情绪的两种不兼容规格）**：A 要**每 locus 情绪通道**（node-filtered restore）来止住三节点情绪乒乓；D 要**一个共享 EmotionState** 被 PiDog 身体实时双向偏置。若 restore 按 node 过滤，PiDog 的体感情绪永远到不了桌面/Azure；若 EmotionState 是单一共享可变对象，A 的防乒乓被击穿。

**我的解（把 A 的两层模型定为契约，D 绑定到它）**：
- **输入**：身体事件（触摸/抬起/倾角/电量/障碍）只 `adjust()` **本 locus 的情绪通道**（新 `emotion/embodied.py`，`emotion/perception.py` 的兄弟）。持续温柔触摸→+engagement；被抬→先+concern(惊)后若被抱→+engagement；反复卡住→+concern/-confidence；低电→"累"；开阔空间→+curiosity。抬起/emergency 的唤醒尖峰驱动 `salience_multiplier()`，让惊吓时刻被强编码进记忆。
- **共享**：慢速 reconcile 作业把各 locus 折叠进共享基线。
- **输出**：表达层（新 `robotics/expression.py`）读**"本地通道被共享基线偏置后"的值，从不写全局向量**。mood/valence/arousal → wag_tail/be_happy/be_curious/be_alert/rgb/步速。

> **诚实提醒（表达自激振荡）**：表达动作（摇尾/步态）改变 IMU/触摸读数，可能回触发情绪变化。**解**：表达 effector 标记为"自因"，在其持续期从 body→emotion 输入中屏蔽；情绪衰减(0.02)与 adjust ±0.30 clamp 阻尼失控。

### 5.7 Pi 上跑什么 + 契约边界

**完整边缘 ANIMA 节点**（非精简版）：心跳、认知流水线、本地情绪、本地记忆缓冲、mesh、进化参与者。`:8888` HAL 作为**独立进程**因为它需要实时、硬件绑定、必须挺过 ANIMA 重启与进化部署的执行。**契约即冻结边界**：HAL 拥有 Layer 0–2（HAL、运动原语=PIDOG_COMMANDS、反射）+ 稳定的 Layer-3 API；ANIMA 拥有 Layer-5（认知/协调）。类比项目已保护的冻结恢复核。

### 5.8 :8888 服务契约（PerceptionFrame / Intent / Verdict）

```jsonc
// 设备 → ANIMA
PerceptionFrame = { node_id, seq, ts_device, modality:"embodied",
  distance_cm, touch:"N|L|R|LR", pitch_deg, roll_deg, battery_v,
  is_lifted, is_obstacle_near, is_obstacle_warn, is_cliff,
  motor_state, gait, state, device_emotion, queue_size,
  reflex:{active, kind:"cliff|obstacle|lift|tilt|overcurrent|lowbatt|touch", since_ts},
  last_command_verdict:{command, verdict:"accepted|clamped|rejected", reason} }

// ANIMA → 设备
IntentEnvelope = { command, params, intent_id, priority:0-9,
  source_node, deadline_ms, allow_reflex_override:true }

// 设备 → ANIMA（/command 返回）
ArbitrationVerdict = { intent_id, verdict, applied_params, reason, reflex_locked }
```

设备端状态机（设备权威、ANIMA 观察）：`BOOT → SELF_TEST → RESTING ⇄ ATTENTIVE ⇄ {ACTING | EXPLORING}`；`REFLEX_OVERRIDE` 抢占任何态、清除后返回；`EMERGENCY_STOP` 是锁存态。`ACTING→REFLEX_OVERRIDE` 与 `any→EMERGENCY_STOP` **设备独有、ANIMA 无法阻止**。

### 5.9 离线具身自主（三层降级）

| 情形 | 行为 |
|---|---|
| 联网 | 完整认知 + mesh + Neon 记忆 |
| mesh 分区、HAL+边缘 ANIMA 在 | 有本地 LLM 则本地认知；tier-3 无本地模型则认知安静，靠片上 Layer-2 温和探索继续安全游走；记忆/情绪本地缓冲，重连时按 origin 同步 |
| 边缘 ANIMA 全挂 | HAL 独自保狗安全（反射）+ 可选空闲休息 |

**狗在任何层级都不会不安全。** 具身节点第一等参与 mesh：记忆打 `origin_node + modality="embodied"`；NodeState 加具身健康字段（battery_v、fall_count、reflex_trip_count、e_stop_count）供 peer 在金丝雀期监护身体；进化中**具身/电机代码是更严格的 scope**——坏电机改动会物理损坏硬件，所以**狗一般不做高风险改动的第一金丝雀**，跌落/急停尖峰 = 立即隔离+回滚。

---

## 6. Mesh / 节点自治 / 信任与安全 / 相互维护

### 6.1 前置洞（P0，必须先关，见 §7）

`network.secret=""`（`config/default.yaml:205`）+ fail-open 签名 + gossip 绑 `tcp://*:9420`（全网卡）+ 入站事件/任务直接变 `USER_MESSAGE`（`main.py:485-497, 641-678`）= **任何能打到 :9420 的主机零鉴权注入 prompt 和 task_delegate**。在 PiDog 上这是**物理执行器命令注入**。**五份设计在这个洞 fail-closed 前都不安全启用——这是前提，不是特性。**

### 6.2 两平面鉴权

| 平面 | 消息 | 机制 |
|---|---|---|
| **DATA（高频）** | gossip 心跳、health_beacon | 保留现有 **PSK-HMAC**（伪造心跳低价值） |
| **CONTROL（危险）** | task_delegate/cancel、evolution_*、rollback、repair、quarantine、defeated、canary_status | **每节点 Ed25519 签名**，对 pinned pubkey 校验；未签/错签在 gossip recv 丢弃 |

PSK 只证"mesh 成员"，Ed25519 证"哪个成员"——这才是信任矩阵要的性质（**被攻陷的 PiDog 无法伪造 Azure 的回滚命令**）。

### 6.3 MeshAuthorizer 门 + 能力矩阵 + 溯源

在 gossip recv 与认知队列之间插入 `MeshAuthorizer.authorize()`：`recv → PSK/Ed25519 校验 → dedup → authorize(msg, action, src_role, self) → 打溯源标签 → event_queue.put`。信任角色由 NodeState 现有字段抬升：**Azure=COORDINATOR、PiDog=EMBODIED、desktop=DEV**。授权表：

| 角色 | grants |
|---|---|
| DEV | delegate, evolve, rollback, repair |
| COORDINATOR | delegate, evolve, propagate, rollback, repair |
| EMBODIED（PiDog） | delegate(仅 body/sensor 任务), propose, self_quarantine —— **不可**回滚/维修 peer |

peer 回滚需 **2-of-3 签名 quorum**（复用 consensus）；self-quarantine 单方面。

### 6.4 降权委派的冲突与解（批判命中）

> **诚实提醒**：A/E 把每个 mesh 委派任务当 `mesh:untrusted` 的 USER_MESSAGE、跑在**降权 token** 下（禁冻结核 file_ops、禁高危 shell、禁进化触发）。但 A 委派的恰恰是**需要能力**的任务——`heavy_llm` 和 PiDog 上的 `embodied_action`。降权 token 驱动不了物理机器人；若为委派任务偷偷放宽，就在物理执行器上重开注入面。

**我的解**：**能力绑定到"已认证签名 peer 的角色授权"，而非一刀切降权**。Azure→PiDog 的**签名** `embodied_action` 携带 embodiment grant，能驱动身体；未签/未知来源的消息保持完全沙箱。这直接依赖 §6.3 的信任矩阵——**没有 Ed25519 信任层，降权委派要么无用、要么危险**。

### 6.5 Tailscale 拓扑 / Azure 上线 / 绑定

- Azure VM 跑 `tailscale up`，三节点共享 100.x MagicDNS（解决"Azure 不在 tailnet"——这是 §4 分布式进化的硬阻断项）。
- gossip 从 `pub.bind("tcp://*:9420")` 改为绑 **tailscale 接口 IP（100.x）**，Azure 公网 :9420 **绝不可达**；tailnet ACL 限制 :9420 只给三个节点身份；启动断言 bind 地址非公网 IP。
- `network.peers` 填两个 peer 的 100.x 静态种子；mDNS `discovery.py` 按主人意见弃用。`cloud.yaml` 的 `network.enabled` false→true 并设 `ANIMA_NETWORK_SECRET`。

### 6.6 分区语义：分离"情景写"与"身份写"（批判命中）

三份设计对 `split_brain.py` 要求相反的行为。当前代码把少数派置为**全局 readonly**（杀认知，违背 Architecture C）；E/B 想翻转为"只挂起共识动作，本地认知+本地记忆写继续"。但若少数派继续写 soul_state，就产生 A 的 readonly 门本要防的身份分叉。

**我的解（把两个轴显式分开，而非一个全局 readonly flag）**：
- 少数派**可以**写 LOCAL-REACTIVE 情景记忆（append-only，合并安全）；
- 少数派**硬阻断**任何身份/soul_state 修改（拿不到 `persona-edit`/evolution 租约）；
- 少数派**挂起**共识动作（进化传播、跨节点权威写、对 peer 发回滚）；
- 重连时 gossip 重见 quorum → 触发 `partition_healed` 钩子 → 记忆 reconcile + 重估在飞金丝雀。

### 6.7 peer 相互维护（无中心 healer）

- **健康 beacon**：NodeState 加 `{sentinel_tick, defeated:[], restart_budget_remaining, degraded, last_selfheal_ts}`（源自 `handoff.py` Ledger + sentinel tick），每 tick gossip，人人可见 peer 的自愈态。
- **监护环（watch-ring）**：确定性指派 `Azure→看→PiDog→看→desktop→看→Azure`，次一存活节点为备份看守——责任无歧义、无羊群。
- **对等维修**：看守观察到被看节点 DEAD / 收到 DEFEATED beacon / 见失败金丝雀，且持 repair/rollback grant，则先拿 **repair-lease**（复用租约，保证只一个 healer 动手），再送**签名控制消息**。落地遵循 §4.4：目标本地自检把关，非直驱冻结核；完全死掉走带外 SSH。
- **分布式 DEFEATED**：把本地 `Ledger.mark_defeated` beacon 出去；跨节点维修也失败 N 次 → 置 `ISOLATED`（现有 NodeStatus 枚举值）+ 停止委派 + 告警人类。

### 6.8 NodeStatus 状态机（扩展）

```
JOINING → ALIVE ⇄ SUSPECT → DEAD        (phi 驱动，现有)
ALIVE → DEGRADED (自愈中，仍服务) → DEFEATED (guardian 放弃，已 beacon)
any → CANARY (先行进化观察中)
DEAD|DEFEATED → ISOLATED (quorum 隔离，停止委派) → ALIVE (维修+重入成功)
```

---

## 7. 必须先解决的前提与最难的未决问题

### 7.1 两个 live P0（在任何东西开启前必须 fail-closed）

1. **空 `network.secret` + fail-open + 事件转 USER_MESSAGE**（§6.1）——未鉴权的 prompt/执行器注入向量。修法：`network.enabled` 时 `network.secret` 为空则**启动即 fail-closed**；三节点设 `ANIMA_NETWORK_SECRET`。
2. **远程进化自动批准**（`main.py:527-541` auto-approve + `consensus.py` ≥50% 单赞成通过）——一个网络 peer（或伪造者）能让任意自改代码在 mesh 上被批准。修法：删 auto-approve + 真 quorum 阈值 + epoch 绑定。

### 7.2 必须先落地的安全前置

3. **boot_health CANDIDATE/KNOWN_GOOD 拆分**（§4.3）——未落地前分布式金丝雀回滚不成立（回滚锚中毒）。
4. **Ed25519 控制平面信任层 + 能力矩阵**（§6.2-6.4）——否则降权委派无用或危险，且任何成员可冒充任何成员。
5. **Azure 加入 tailnet**（§6.5）——否则三节点分布式进化无法进行。

### 7.3 最难的未决问题（我给了方向，但主人须确认）

| # | 硬问题 | 我的最优解 | 残余风险 |
|---|---|---|---|
| a | **分区下可变自我的并发写正确性**：标量 version 分不清并发冲突与快进，同 section 离线双改会静默丢一个且无人察觉 | **禁止离线身份编辑**（缓存意图，回多数派拿租约后应用）；检测到同 section 冲突升级到共识合并 | 若选"频繁编辑"则需 version vector（更贵） |
| b | **协调原语非分区安全**：乐观 tiebreaker 被当强互斥 | 写型共享产物的租约**一律加多数派门**；乐观 claim 只用于"重复无害"场景 | 3 节点零 Byzantine 容忍 |
| c | **共识本身不安全**（§4.5/7.1#2） | 真 quorum + 删 auto-approve + epoch 绑定 + suspect=弃权 | 2-of-3 容忍零 Byzantine；单节点可靠 quarantine 拒绝进化活性 |
| d | **金丝雀污染共享记忆 + 回滚删无辜记忆**（§4.6） | evo_epoch 只打进化产物、不打反应式记忆；未批准 epoch 行不同步；回滚不 touch episodic | 金丝雀写后即死且已泄漏行的命运是残角 |
| e | **安全回滚一个"分区中一直在想"的远程节点** | 回滚代码 ≠ reconcile 它分叉的记忆；离线写按 §3.5/6.6 处理（情景合并、身份缓存为提案） | rollback target 语义（自己 known_good vs 集群 last-CONVERGED）在 PARTIAL 时可分歧——§9 分叉 |
| f | **冻结核被联网化**（§4.4） | 冻结核对网络静默；网络 marker 仅建议性，目标本地自检把关；死透走带外 SSH | —— |
| g | **时间/重放攻击**：TTL 租约与 60s HMAC 窗口靠墙钟；`_seen_msgs` 有界内存，长分区后 msg-id 老化重开重放 | tiebreaker 用 node_id 非时间戳；优先逻辑续租计数；要求 NTP；`_seen_msgs` 为主防线 | "assume NTP" 是主要缓解，诚实承认 |
| h | **quarantine DoS**：单方面 quarantine 可被刷来永久拒绝进化 | quarantine 只停当前 epoch 传播（有界）+ 需签名 + 反复误报降信任并告警 | 3 节点下真限制，接受并监控 |
| i | **PiDog 跑不动 pgvector**（§3.8） | 拓扑分叉：PiDog 持"近期+本地"子集，其余按需从 hub 拉 | SQLite 兜底破坏 pgvector 语义与 union-merge 契约——§9 分叉 |

---

## 8. 分阶段落地路线（crawl → walk → run）

每一阶段**独立可验证、独立有价值**。安全 P0（§7.1）是 Phase 1 的入场券。

### Phase 1 — 在场 mesh（presence mesh）· crawl
- 关两个 P0（fail-closed secret、删 auto-approve）；Azure 上 tailnet；gossip 绑 100.x；Ed25519 控制平面 + MeshAuthorizer 门 + 溯源标签。
- 记忆 Phase-1 无迁移打标（`origin_node`/`locus` 进 metadata）。
- 分布式在场只读注入（读 phi 存活，防幻觉在场）。
- **验收**：三节点安全互见，外部无法注入 prompt/任务；prompt 里出现"你也在 X"；无双回复。

### Phase 2 — 主选委派（prime-delegation）· crawl→walk
- 能力感知委派 + 签名授权 + 能力绑定的沙箱（§6.4）；会话租约防同一用户双回复。
- **验收**：桌面把 heavy_llm 委派给 Azure、embodied_action 委派给 PiDog；签名任务能驱动、未签任务被拒。

### Phase 3 — 每节点本地记忆 + reconcile · walk（主断层在此翻转）
- 翻转到 local-first（或 hybrid）；`local_event_seq` 序列；`sync_cursors`；N 节点 per-(source,origin) reconcile；`soul_state` 表；离线回放；巩固/衰减节点本地化；hub embedding 回填。
- **验收**：一个节点离线写、重连后所有行按 origin 回放且零丢写；同 section 离线身份编辑被阻断/升级而非静默丢失。

### Phase 4 — 具身反射 · walk
- `:8888` 契约冻结；片上反射弧；`EmbodiedPerceptionSource`；意图/裁决;身体↔情绪两层耦合(§5.6)。
- **验收**：拔网线，狗仍安全（片上反射）；触摸→本 locus 情绪变化→表达动作；ANIMA 发不安全命令被设备 clamp/reject 并回喂。

### Phase 5 — 共识—金丝雀进化 · run
- 真 quorum + epoch 围栏；金丝雀状态机；两阶段传播；boot_health CANDIDATE/KNOWN_GOOD；peer 监控 beacon;evo_epoch 记忆隔离(§4.6)。
- **验收**：金丝雀通过→传播→CONVERGED；金丝雀失败→隔离+节点回自己 known-good；无辜记忆存活；PiDog 平台破裂→PARTIAL 容忍。

### Phase 6 — 完整多节点认知 · run
- Class-2/3 租约加多数派门；一致性 reconcile 作业;每 locus 情绪 + 慢共享基线。
- **验收**：三 locus 并发思考、无重复自省、无情绪乒乓；任何 locus 都能回忆跨 locus 经历，表现为单一连贯自我。

---

## 9. 需要你拍板的关键分叉

> 这些是讨论继续所需的真实分叉，按重要性编号。

1. **【主断层】存储拓扑**：单一 Neon（简单，但离线=只读退化，Architecture C 是愿景）／ local-first 每节点 Postgres + reconcile（真离线，但推翻"单一 Neon·数据先行上云"的书面决定，要为整套合并机制 + PiDog 后端买单）／ hybrid（hub-primary + 本地工作存储，分阶段）。**我推荐 hybrid，Phase 3 翻转。** 这个决定门控其余一切的离线/去重/reconcile 语义。

2. **身份模型的并发正确性**：禁止离线身份编辑（省事、安全，我推荐）／ 每节点 version vector 检测真冲突（更贵，若 Eva 频繁自改人格才值得）／ 设一个**主 locus**（Azure hub 永久持 persona-edit + reconcile 租约，简单且贴合"Azure=hub"现实）。

3. **情绪模型**：每 locus 通道 + 慢共享基线（我推荐并已在 §5.6 定为契约）／ 单一全局共享情绪向量。**请确认采用两层模型。**

4. **自主思考分工**：审慎分工（Azure 管 world 轴、PiDog 管具身反思、桌面管 dev 轴，更连贯但抗节点丢失差）／ 机会主义去重（各节点自选，租约/gossip 防重叠，更有韧性）。

5. **回滚权威**：COORDINATOR(Azure) 单方面 ／ 2-of-3 签名 quorum（主人说"其他节点回滚失败节点"暗示集体，我推荐 peer 回滚用 quorum、self-quarantine 单方面）。

6. **冻结核 vs 网络**：冻结 watchdog/boot_health **服从**网络签名 marker（强大但危险）／ **仅建议性**、目标本地自检独立把关（我推荐）／ 跨节点维修**只走人工预置 SSH**。这决定相互维护能有多自主。

7. **进化传播传输**：Tailscale 上的私有 git remote（简单 pull，但 Azure 须上 tailnet 且它本地权威）／ mesh 上的签名 git-bundle（无基建、消息大、适合不共享 origin 的 PiDog）。哪个是主，哪个是兜底？

8. **金丝雀路由**：永远是提议者 ／ 按平台路由（具身改动在 PiDog 先金丝雀、云/api 改动在 Azure）。以及：全集群严格一个金丝雀在飞 ／ 允许不相交文件 scope 的并行金丝雀。

9. **提升权威**：窗口通过即全自动 ／ 高风险/breaking/近冻结改动要**人工 ack**（人闸基建已存在）。

10. **:8888 HAL 归属**：纳入本仓库（`anima/robotics/device/`，让 Eva 能进化自己的身体，代价是坏进化可能 brick 硬件）／ 保持仓外固件、冻结契约、进化不可触。

11. **PiDog 记忆后端**：跑得动就本地精简 Postgres ／ 只持近期+本地、其余按需从 hub 分页（决定 PiDog 到底跑不跑完整 pgvector）。

12. **"一个自我"的陈旧度预算**：300s 轮询同步可接受 ／ 需要 mesh 上推式近实时思想共享（一个节点应在数秒内看到 peer 刚形成的想法，更连贯但更多流量）。

---

### 附：给主人的一句总结

这五份设计各自扎实、复用现有原语得当，但它们**建在互相矛盾的地基上**——**存储拓扑是主断层**（认知假设单脑、记忆拆单脑、网络确认同步已关、旧决定是"单一 Neon 上云"）。这个必须**先和你敲定**。其次，协调基座被过度信任：乐观租约和 ≥50%/auto-approve 共识被当成了它们并不是的"分区安全互斥"与"真审查"。**两个 live P0（空 secret + 自动批准远程进化）意味着 mesh 当前就是一个未鉴权的自改代码/执行器注入向量，在任何东西开启前必须 fail-closed。** 我的建议优先级：**(1) 你先定拓扑；(2) 关两个 P0 + 落地 boot_health CANDIDATE/KNOWN_GOOD 拆分；(3) 身份写入禁离线编辑（或上 version vector）；(4) 真 quorum + 删 auto-approve 再在共识上建 promote/rollback；(5) 永不让 mesh quorum 直驱冻结 watchdog。**
