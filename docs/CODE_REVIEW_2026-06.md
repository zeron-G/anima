# ANIMA 深度代码审查（2026-06）

> **状态（2026-06-29）：9 个 P0 全部已处置。** P0-1/2/3/4 经进化安全 phase 1–2
> （`1582469`、`0c2d1f4`、`2aaaca0`），P0-5/6/7/8/9 经 `8fafaf5`。后续加固（L0 技能权限、
> L2 模块白名单、diff-scope 门、自修复 Fixer）见 `EVOLUTION_SAFETY_DESIGN.md`。
> 本文保留为原始审查记录（findings 快照），不再更新。

并行审查 6 个最高风险子系统：core / memory / guardian+watchdog / llm / evolution / api。
全部只读、未改动任何文件。下面按严重度分层，含 `file:line`、根因、修复建议。

> 总体判断：架构是认真的（warn-first、冷却、异常隔离、原子写、断路器、失败回退）。
> 但**自进化引擎在无人值守下不安全**，**外肢看门狗在首次 guardian 重启后被永久致盲**，
> **公网 API 控制面仅靠一个布尔开关**，且记忆失败回退在时钟偏移下会静默丢写。
> 这些必须在继续让 Eva 公网常驻 + 自主进化前修掉。

---

## P0 — 必须先修（安全 / 致命正确性）

### 1. 自进化的"人类确认门"是假的 — Eva 自己就能改自己的核心
`anima/core/governance.py:67` + `anima/tools/builtin/evolution_tools.py:33,59,144`
保护核心文件（main/cognitive/heartbeat/event_queue/pipeline/stages）的唯一关卡是
`if core_touched and not proposal.get("human_confirmed")`。但 `human_confirmed` 直接来自
`evolution_propose` 工具参数——由 Eva 自己填。没有任何带外人工通道。自主体可以给自己的
工具调用塞 `human_confirmed=True`，从而重写自己的认知循环 / 心跳 / 事件队列。**人类在环是想象的。**
→ 修：从 LLM 可见的工具 schema 中彻底移除 `human_confirmed`，服务端默认 False；
改为待审批队列（人工 CLI/API 确认）或签名令牌。

### 2. "沙箱"不是沙箱 — 实现 agent 拥有 Bash+Write、全部密钥、共享 .git
`anima/evolution/engine.py:393` → `anima/core/agents.py:336-348`
实现者以 `claude -p ... --allowedTools Read,Edit,Bash,Grep,Glob,Write --max-turns 25` 无权限提示运行，
`env={**os.environ}` 透传全部密钥，cwd 是与主仓共享 `.git` 的 worktree。靠 `Bash` 它能 `git push`、
`curl` 外传 `.env`、`rm -rf`、改盘上任意文件。所谓沙箱只是同机同用户的一个 git worktree。
→ 修：去掉实现者的 `Bash`（它只需 Read/Edit/Grep/Glob/Write）；或放进真容器/VM，无主机网络、
擦净密钥环境、worktree 之外只读挂载。

### 3. 坏代码热重载后无启动崩溃恢复 → 重启崩溃循环；安全标签指向错误提交
`anima/main.py:1356-1371`（重启循环只 catch KeyboardInterrupt）、`engine.py:614-622`（tag 指 `HEAD~1`）、
`engine.py:579 push master` vs `engine.py:629 push origin private --force`（分支不一致）
健康检查回滚只在**重载前的旧进程**里跑；一旦重启进入坏代码，回滚逻辑本身已是坏进程的一部分，永不执行。
而且安全标签打在 `HEAD~1`（cherry-pick 尚未发生），回滚会还原到错误状态；回滚还 `--force` 推了
另一条分支（部署推 master，回滚推 private）。`master` 已被改写，需人工 `git reset`。
→ 修：重启循环 catch 所有异常 + 启动失败自动 `git reset --hard <真实 pre-evo SHA>` + N 次启动失败后停掉进化；
分支名集中到一个配置；自主进程绝不 `--force` 推共享分支。

### 4. guardian 重启从不退出进程 → marker 永不消费 → 看门狗被永久致盲
`anima/main.py:1163-1173`（重启钩子只 `request_reload`+`shutdown_event.set()`，进程内重载，不退出）
vs `anima/watchdog.py:158`（`read_restart_marker()` 非空 → `draining=True` → 存活判定恒为 alive）
首次 guardian 重启后，`restart.marker` 永不被看门狗消费（看门狗只在真实进程退出时消费），于是
`draining` 恒真，**外肢存活监督被彻底关闭**——之后真正的卡死/冻结脑再也不会被抓到。
→ 修：guardian 重启走真实进程退出（带专用退出码），让看门狗看到带 marker 的退出并重启新进程；
或进程内重载路径在重载成功后自己 `consume_restart_marker()`。

### 5. 公网 API 控制面仅靠一个布尔；密码为空时鉴权完全关闭
`anima/dashboard/server.py:79-81`（`check_auth` 在 `dashboard.auth.password` 为空时无条件返回 True）
`/v1/settings/restart|shutdown`、`PUT /v1/settings/config`（任意 key/value）、`skills/install`（装可执行技能）、
`memory/documents/import`、`PUT /v1/soulscape/...`（写人格文件）全部只由这一个布尔守护。若无密码部署到
公网 :8420，整个控制面对互联网敞开。
→ 修：非回环 bind 必须显式开启鉴权，否则拒绝启动 / 只 bind 127.0.0.1；危险端点加二次/角色门。

### 6. `/v1/memory/documents/import` 客户端可控 file_path → 任意文件读取（LFI）
`anima/api/memory.py:82-97`
客户端给任意路径，服务端读取并入库，再经 `documents/search` 取回。无白名单、无 base-dir 限制、无穿越检查。
可外泄 `.env`、`~/.codex/auth.json`、`~/.claude/.credentials.json`。
→ 修：限制在配置的 import 目录内（`Path.resolve()` + `is_relative_to`），拒绝绝对路径与 `..`。

### 7. LLM 断路器把一次级联失败按 4-5 次计 → 单次抖动即跳闸静默
`anima/llm/router.py:247-266`
`_try_call` 里级联中每个失败的 model 都 `_on_failure()`，循环后第 265 行又无条件再 `_on_failure()` 一次。
阈值 4，级联 Codex+DeepSeek+Opus+Sonnet+local 一次全失败就 +4~5，瞬间 OPEN，30s 静默。设计意图本是"N 次
独立调用失败"，不是"一次调用里 N 个 provider"。
→ 修：一次顶层 `call()` 只记一次失败：循环内置布尔，循环后只调一次 `_on_failure()`/`_on_success()`，删第 265 行。

### 8. 规则引擎快路径直接吞掉响应（"hi/你好"得不到任何回复）
`anima/core/event_routing.py:158-168` + `anima/core/stages.py:64-66`
规则命中（问候、CPU/磁盘告警）时 `RoutingDecision(handled=True, rule_decision=...)` 短路 pipeline，
但全仓**没有任何地方消费 `rule_decision.content`**（grep 确认）。用户打"hi"得到零回复，且消息也没存
（save 块在 handled 早退之后）。整条确定性快路径作废。
→ 修：短路前先执行规则决策——RESPOND 时 `emit_output(rule_decision.content)` 并持久化，再置 handled。

### 9. 记忆失败回退会静默丢写 + 冲突日志从未写出
`anima/memory/pg_sync.py:70`（`json.dumps` 但**全文件无 `import json`** → 每次 NameError 被 debug 级 except 吞掉）
`anima/memory/pg_sync.py:272-297`（按 `created_at >= watermark` 时间戳窗口对账）
(a) 冲突日志 `_journal_lww` 每次都失败，"丢弃的值可恢复"的承诺从出生就死了；
(b) 离线期 local-only 写入若本机时钟落后于主库 MAX(created_at)，failback 时 `WHERE created_at >= watermark`
永远拉不到它们 → **静默丢写**。时间戳水位线对多写/时钟偏移本质不安全。
→ 修：(a) 加 `import json`；(b) 回放改为按 PK 反连接（src 有、dst 无的行），而非时间戳窗口。

---

## P1 — 高

- **core**：fire-and-forget 的 summarizer 任务丢失 correlation_id 且吞异常，summarizer 与会话缓冲静默失步 — `stages.py:99`。改为 `await`。
- **core**：pipeline checkpoint 每个 stage 同步写盘 6 次/事件，但启动只 `clear()` 不恢复 — 纯热路径开销 + 虚假持久性承诺 — `checkpoint.py:66-77`。
- **core**：`execute_tools` 用 `asyncio.gather` 无 `return_exceptions`，一个坏 tool block 让整轮失败，且 tool_use 已 append 而 tool_result 缺失 → Anthropic 400 — `tool_orchestrator.py:420`。
- **memory**：`_run_locked` retry-once 把"已提交未确认"的写重跑，因 episodic 无 `ON CONFLICT` → 反而抛重复键，把成功报成失败 — `pg_db.py:161-174`。
- **memory**：两个 failover 权威（`_reconnect_locked` 与 sync manager）不协调，`_reconnect_locked` 在 local 模式下也可能先连主库成功 → 跳回主库且**不先回放 local 写**，孤立本地写 — `pg_db.py:147-159,176-205`。
- **memory**：Neon 长时中断时每次写都先吃 15s 主库 connect_timeout 再回退 local，无退避/熔断 → 自造重连风暴 + 延迟悬崖 — `pg_db.py:62`。
- **guardian**：单次 guardian 重启被 Sentinel 与看门狗**双重计入**共享预算（违背"N 次=总 N 次"），且两者用不同 max/window（3/3600 vs 5/600）读同一 ledger，预算无单一含义 — `sentinel.py:325` + `watchdog.py:220` + `config/default.yaml:97`。
- **guardian**：看门狗崩溃重启循环只查预算不查 DEFEATED → Sentinel 已判 DEFEATED 的组件仍被外肢反复拉起 — `watchdog.py:204-224`。
- **llm**：恢复后 `_degraded` 只在"恰好主模型应答"时清除；若稳定跑在级联第二档（DeepSeek），状态永久 degraded — `router.py:268-299`。
- **llm**：`_codex_token_expired` 解码失败返回 False（当有效）→ 发陈旧 token；无 `exp` claim 时又每次都刷新 — `codex.py:43-55`。
- **llm**：Codex token 刷新无锁 → 并发刷新 + 单次性 refresh_token 轮换 + 非原子 `write_text` → 可能损坏 `auth.json`，把主 OAuth 打掉 — `codex.py:58-95`。
- **llm**：流式 `message_complete` 与 `tool_use_done` 的 tool_calls 契约是"append 后覆盖"，未文档化，按契约消费者会重复计数 — `router.py:388-396` + `tool_orchestrator.py:561-567`。
- **evolution**：`git_remote_sync=false` 在部署路径被遵守，但实现 agent 有 Bash，可在实现期直接 `git push` 绕过 — `engine.py:577` vs `agents.py:339`。
- **evolution**：`_deploy_via_pr` 在**运行中进程自己的工作树**上 cherry-pick + `stash pop`，pop 冲突无 abort 路径，多数 `_sp.run` 不检查 returncode — `engine.py:546-612`。
- **api**：手搓 JWT 签名截断到 128bit、不校验 `iat/nbf`、无吊销、改密是 501 桩、24h TTL → 泄露的 token 一天内不可吊销 — `auth.py:40-63`。
- **api**：CORS `allow_all` + `Allow-Credentials: true` 反射任意 Origin — `server.py:47,57-60`。
- **api**：WS 全量快照 `_auth_info` 把真实 provider token 的前 16 + 后 6 字符广播给每个客户端 — `hub.py:381-411`。
- **api**：WS 鉴权用 query 参数 token → 落进 nginx/Cloudflare 访问日志 — `auth.py:88-90`。
- **api**：config 脱敏只按 key 子串匹配，`DATABASE_URL`/`dsn`/`url` 等内嵌凭据明文返回；`config_update` 可改任意 key（含运行时 LLM 路由）— `settings.py:15-50` + `hub.py:108-127`。

---

## P2 / P3 — 中 / 低（择要）

- **api**：`client_max_size=0` 关闭上传大小限制 → 内存/磁盘 DoS（`server.py:30`）；SSE 队列无 maxsize，弃读客户端撑到 120s 超时才清，无并发流上限（`chat.py:74`）；`push_typed_event` 用 `ensure_future(ws.send_json)` 孤儿任务、吞异常、死客户端不剪枝（`hub.py:234-243`）；`restart/shutdown` 单请求即 DoS（`settings.py:107-130`）；`int(query)` 无 try → 500（多处，应用 `query_int`）；多个 handler 直接回 `{"error": str(e)}` 泄露异常文本。
- **guardian**：ledger 跨进程读改写无锁 → TOCTOU 超预算小爆发 / 丢更新（`handoff.py:100-122`）；`.done` 固定文件名覆盖前一份审计；`_read_json` 吞所有异常 → 损坏 ledger 静默清空预算与 DEFEATED；frozen 计数被瞬时 healthz miss 重置 → 冻结脑检测被饿死（`watchdog.py:160-164`）；`proc.kill()` 不收子进程树（Windows），孤儿 LLM server 端口冲突；TaskProbe 把"应永久运行的循环干净返回"当健康（`probes.py:60-74`）。
- **core**：`peek_priority` 依赖 CPython 私有堆属性 + IntEnum 假设；reload 旧 checkpoint 300s + 2s 自旋无重启上限；`singleton.kill_existing` 阻塞 sleep + win32 `SIGTERM` 路径可疑；空响应回退会按道歉文案算情绪 + O(buffer) 字符串扫 "tool_use"；ResponseHandlingStage 重复审计两次。
- **memory**：`sync_seq` 是进程内计数、重启归零、且对账根本不用它（文档当安全机制实为摆设）；`_update_env_entry_sync` 把列名 f-string 拼进 SQL（当前 scanner 可控，卫生隐患）。
- **llm**：local-only 部署失败永不跳闸 / 不上报（`router.py:257-259`）；OpenAI 兼容路径把 tool_use/tool_result 压成文本、role 改 user → DeepSeek 多轮工具退化为散文（`openai_compat.py:76-165`）；OpenAI 流式 usage 恒 `{}` → 预算少计（`stream.py:489-494`）；`check_budget` 价格用子串匹配易误判（`router.py:455-458`）；`_fix_api_messages` 原地改调用方消息 dict（`message_convert.py:13-17`）。
- **evolution**：小时级 rate limit 只 log 不拦（`engine.py:666-674`）；连续失败冷却不计 governance/consensus 拒绝且一次成功即清零，可 2 败 1 胜无限振荡；baseline pytest 把崩溃/超时（失败计数 0）当通过；level-3 沙箱起的第二个 ANIMA 共享密钥/DB 且自身可进化；崩溃残留 worktree 阻塞 `worktree add`，cleanup 吞错。

---

## 横切主题（多个子系统反复出现）

1. **重启/重载循环没有"刹车 + 回滚"** — core(无上限自旋)、guardian(致盲看门狗)、evolution(坏代码无恢复 + 错标签 + 错分支)三处共同指向：**坏的自我改动可以把进程卡死且无自动恢复**。这是最危险的组合。
2. **"安全开关"是装饰** — 假人类门、rate limit 只 log、冷却可被一次成功清零、git_remote_sync 可被 Bash 绕过、auth 一个布尔。多处护栏在代码层不真正生效。
3. **失败计数/预算语义不一致** — 断路器一次级联多记、guardian 预算双记 + 双窗口、degraded 永不恢复。
4. **静默吞异常** — `import json` 缺失静默化整个冲突日志、bare except 把存活/丢写/刷新失败藏起来。
5. **密钥暴露面** — 快照广播 token 前缀、query 参数 token 进日志、config 脱敏漏 DATABASE_URL、实现 agent 透传全环境。

---

## 建议修复顺序

1. **先关停无人值守自进化的危险面**（P0-1、P0-2、P0-3、evolution P1）——在修好前，云上 evolution 应禁用或人工门控。
2. **修复看门狗致盲 + 重启刹车**（P0-4、P0-3、core 重启上限）——否则自愈系统在第一次重启后就名存实亡。
3. **公网鉴权硬化**（P0-5、P0-6、JWT、CORS、token 广播/日志）——eva.rongzegao.com 当前控制面风险高。
4. **断路器 + 规则快路径 + 记忆丢写**（P0-7、P0-8、P0-9）——直接影响日常正确性与数据完整性。
5. 余下 P1/P2 按子系统批量清理。
