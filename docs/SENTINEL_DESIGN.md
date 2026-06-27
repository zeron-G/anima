<!-- ANIMA Sentinel 自愈系统设计方案 v2.1 — generated from a design+red-team workflow.
     Status: PROPOSAL, decisions locked (see "决策落定与修订 v2.1"). Not yet implemented.
     Build order: §8 phases P0→P5. -->

# ANIMA 统一自愈系统 — 最终硬化方案 (Sentinel / Watchdog + Fixer, 方案 v2.1)

> 设计铁律（贯穿全文，红队 mitigation 已折叠入正文，非附加）：
> 1. **单一修复权威**：进程级重启与代码修复在任意时刻只有一个 owner。Sentinel 决策，外肢执行；不存在两个控制器对同一故障各自动手。
> 2. **观察优先**：LLM 级联、DB 故障转移、电路熔断器本就会自愈——它们向上**汇报**，Sentinel 默认只**预警**，不抢方向盘。
> 3. **同锁写入**：任何改写共享状态（router 熔断计数、`using_local`、代码、写栅栏）的执行器，必须走与自愈者相同的锁；绝不新增第二个无锁写者。
> 4. **每个可逆挡位都有回退/拒绝路径 + 独立且更紧的预算**；不可逆挡位（进程重启、代码修复）必须由**已确认**（非抖动）故障触发，且对自愈 TCB 文件零接触。
> 5. **每次改动都要真启动一次 app 验证**（项目铁律：pytest 漏运行期接线 bug）。

---

## 决策落定与修订 (v2.1 — 2026-06-27，覆盖正文相应处)

用户已就 §9 决策拍板，以下修订**优先于**正文中相应描述：

1. **自动优先 + 可配人工授权**：每个 Fixer 挡位带 `mode: "auto" | "manual"`，**默认 `auto`**（尽量全自动）。`manual` 时该挡位不直接执行，而是产出一个 `RepairProposal` 写审计并经 SYSTEM_ALERT 呼人，等显式 `approve(action_id)` 才执行。DB 加速切回（`recover_now`）、进程重启、代码自修复**全部默认 auto**，运维可在 `[guardian.<component>] mode="manual"` 单独翻成需授权。这取代正文里各处"默认关/默认 confirm-only"的保守默认——除了下面第 2 条。
2. **删除外肢 startup `claude -p` 兜底**：§4(f) 末与 §1.1 中保留的"外肢 startup-crash-loop `claude -p`（默认关）"**整段删除**。理由：已有 evolution 管线，对一个连 Sentinel 都启动不了的进程做盲改、且无观察、回滚依赖 git 本身健康——风险收益不成比例。`[guardian.limb] startup_claude_p` 配置项一并移除。稳态代码修复**只**走 §4(f) 的 evolution 管线挡位。
3. **代码自修复默认全自动**：白名单目录内、双信号复现、consensus 基质健康、管线测试全绿 → **自动 cherry-pick + 热重载**（`[guardian.code] mode="auto"`）。白名单外/触及 TCB/核心/测试 → 恒为 `ALERT_ONLY`（这条铁律不可被 `mode` 覆盖）。`mode="manual"` 时即便管线全绿也只提议、等人批。
4. **进程重启默认自动**（预算内，`max_restarts=2/window` 后进 DEFEATED 呼人）；`mode="manual"` 可改成每次重启先人确认。

### 脑裂与合并方案修订（覆盖 §5 Part 2）

利用"所有节点共享一个云 Postgres"这一架构事实，**偶分裂（2/2 等对等分区）基本被消解**，按三种情况：

- **纯 gossip 分区（节点互断但都连得上云库）**：只有一个数据主（云库），两边写都落同一库，append-only 天然不冲突 → **无需合并、无需栅栏**。
- **协调冲突（两节点都以为自己负责同一会话 → 重复回复）**：无法"合并"，必须预防。🔨 **把会话归属从 gossip 投票锁改为云库 `session_lease` 行**（owner + 过期，原子 CAS 抢占）——共享 DB 成为唯一仲裁者，从根上消除"两个主"。比硬只读栅栏更优雅、天然合并友好。落 **P3**，是对 `SessionRouter` 的改造（当前为 gossip 协调、内存无锁）。
- **双重故障（分区 + 丢云库 → 降级本地 PG，产生本地独有写）**：**唯一真正要合并的场景**，重连时做 union 合并（见下）。

**数据合并规则（重连时，local→primary）**：
- append-only 表（episodic/emotion/documents/llm_usage）：按 `id` 求并集 + `ON CONFLICT DO NOTHING`（现有 PgSync 回放，幂等无冲突）。
- `static_knowledge`（唯一可变表）：🔨 加 **`version BIGINT` 单调版本计数器**定胜负（**不用墙钟**——多机 `time.time()` 不同步会确定性丢正确编辑）+ **tombstone 墓碑**处理删除（防"删除复活"）+ **败者旧值写 `guardian_actions.jsonl` journal**（node 作用域，可人工恢复）。
- **不变量**：合并是 union；可变行冲突的败者留痕、不静默覆盖；切回前 replay 必须完成（§4b 已保证）。

**硬只读栅栏退化为最后手段**：仅"分区 + 丢云库 + 本地不可安全合并"极端叠加时才用，且优先"少数派写 spool 暂存、愈合重放"而非直接停写。原 §9 决策 5（2/2 谁只读）由此**消解**。

---

## 0. 总览图

```
                         ┌──────────────────────────────────────────────────────────────┐
                         │                  SENTINEL  (进程内大脑)                          │
                         │              anima/guardian/sentinel.py  🔨                     │
                         │                                                                │
   报告上行 (report up)    │  • on_report(Signal)：pull+push 单一漏斗                          │
   ┌────────────────────►│  • 每子系统 FSM: OK→预警WARN→DEGRADED(自愈观察)→REPAIRING          │
   │                     │              →RECOVERING→ESCALATED→DEFEATED(陷阱态,持久化)        │
   │                     │  • 全局关联抑制：≥2 子系统同时 ERROR + 资源信号 ⇒ 单一 CRITICAL      │
   │                     │  • 预警 (heartbeat.py:213 SYSTEM_ALERT 复用) → 反复无效才升级       │
   │                     │  • 暴露 sentinel.snapshot() → /v1/status                        │
   │                     └───────┬──────────────────────────────────────┬─────────────────┘
   │                  decisions  │ (升级,握同一把锁)            self-liveness │ tick 写盘
   │  ┌──────────────────────────▼───────┐   ┌───────────────────────────▼──────────────┐
   │  │  WATCHDOG 层 (传感器, 只读被动)       │   │  FIXER 层 (执行器, 闭接口 can_handle/        │
   │  │  anima/guardian/watchdog/*.py 🔨   │   │  repair/rollback) anima/guardian/fixer/*  │
   │  │  • TaskProbe (push:add_done_cb)   │   │  挡位(轻→重, 越界即拒):                       │
   │  │  • LlmProbe  (pull:get_status)    │   │   1 llm_backstop  ✅观察+ensure_local        │
   │  │  • DbProbe   (pull:db.status)     │   │   2 db_recover    ✅recover_now(同锁)        │
   │  │  • MeshProbe (gossip tick 活性)    │   │   3 restart_task  ⚠️须 restart()契约         │
   │  │  • ChannelProbe ⚠️INFO-capped     │   │   4 restart_proc  🔒外肢(软重启,严格)         │
   │  │  • ResourceProbe                  │   │   5 code_repair   ⚠️evolution管线,denylist  │
   │  │  • LogPatternProbe (jsonl,非log)   │   │   (mesh/channel = alert-only)             │
   │  └───────────────────────────────────┘   └───────────────────┬───────────────────────┘
   │       ▲ 子系统自愈即汇报                                       │ "请重启我" (原子marker)
   │       │ (set_degradation_listener / using_local / failover)  │
   └───────┴────────────────────────────────────────┐  ┌─────────▼──────────────────────────┐
                                                     │  │  EXTERNAL LIMB (不可约外肢)            │
                                                     │  │  anima/watchdog.py (瘦身)  🔒          │
                                                     │  │  • 唯一拥有 Popen+poll 生命周期          │
                                                     │  │  • 自主活性: poll/loopback liveness/    │
                                                     │  │    hb-file age (三信号合议, 非单点)       │
                                                     │  │  • 服从 Sentinel: marker→优雅重启        │
                                                     │  │  • 硬兜底: 无 marker 的崩溃/僵死才自主杀   │
                                                     └──┤  • 唯一的 startup-crash claude-p(默认关) │
                                                        └──────────────────────────────────────┘
```

**数据流一句话**：传感器（Watchdog 层，只读被动）检测异常并 `Signal` **上报** → Sentinel 通过单一漏斗 `on_report` 收口，先升 **预警(WARN/预警)** 但不动手 → 子系统自身的自动切换（LLM 级联/DB 故障转移）以 `self_healed=True` 报上来，进入稳定 `DEGRADED` 观察态而非修复 → 仅当预警**反复无效**（持续 N tick 或跳 ERROR）且故障被**独立双信号确认**，才升级 → Fixer 从最轻挡位起按阶执行，每挡有冷却+预算，重启/代码修复为最后手段且走外肢/evolution → 恢复需 `RECOVERING` 探针窗内**持续健康**（迟滞）才回 `OK`，否则爬升不下滑 → 全程每一步（report/warn/escalate/repair/rollback/recover）写 append-only `guardian_actions.jsonl` 审计，`DEFEATED` 与预算消耗持久化跨进程，并经 SYSTEM_ALERT 呼人。

---

## 1. 分层架构 & 进程存活

### 1.1 诚实的核心矛盾与解法

进程内的大脑**无法重启自己所在的进程**。事件循环僵死或解释器段错误时，没有进程内代码能跑起来恢复它。`anima/watchdog.py` 作为独立进程存在正是为此（`subprocess.Popen([python,-m,anima])` watchdog.py:377；僵死检测 hb-file 120s + 3 次合议 watchdog.py:461-474）。

**解法——按职责拆分外部 watchdog，而非删除它，并反转权威**：

| 今天 watchdog 的职责 | 折叠去向 |
|---|---|
| 检测死亡/僵死 (poll / hb-file / log-grep) | **语义检测全部进 Sentinel**（对真实对象有类型化访问）；外肢只保留它独有的**活性检测** |
| 崩溃后重启进程 | **不可约外肢**——唯一能在主进程死后存活的东西 |
| `claude -p` 自动改代码 (watchdog.py:176-221, 484-491) | **降级**：稳态路径走 evolution 管线（Fixer 挡位 5）；外肢只保留 **startup-crash-loop 兜底**，**默认关闭** |

**权威反转（这是与现状最关键的差别）**：今天外部 watchdog *自己决定* 重启**且**自己跑 `claude -p`（watchdog.py:484-491），与未来的 Sentinel 会就同一故障各自动手——**两个修复大脑**。新设计中**Sentinel 决策，外肢服从**：

- **软重启（常态）**：Fixer 写**原子、带 PID、带 epoch** 的 restart marker（`os.replace` 写入，含 `phase:"draining"+ts+pid+reason+fault_id`），随后走**既有 evolution-reload 退出路径**（main.py:1186-1188 设 `restart_requested` + `shutdown_event.set()`）。
  - ⚠️ **关键修正（折叠 layering A1/A2 与 fixer A2）**：watchdog.py:425-427 证实 **clean exit (ret==0) 会让 watchdog 直接 `return` 停掉自己**。因此"优雅退出靠 ret==0"是行不通的——会杀死监督者且永不重启。软重启**必须以非零'请重启我'码退出**，外肢读到有效 marker（PID 匹配 + ts 在本周期 + 未过期）才认定为"requested_restart"并重启；marker 读后用 `os.replace`-to-archive 归档（非 unlink-then-read）。
- **硬重启（崩溃/僵死）**：Sentinel 已死或卡住，无法发信号 → 外肢回落到独有的自主检测。

**外肢在软重启期间的"优雅排空窗"（折叠 A1）**：marker 一旦存在，外肢在 `shutdown.timeout_s + slack`（如 60s）内**抑制全部自主僵死/活性检测**，只保留 `poll()` 退出检测。否则排空期间 Sentinel 任务被 cancel、`/v1/status` 后端已死、hb-file 停写，外肢会误判僵死并 `proc.kill()` 在 `pg_sync.stop()` / `memory_store.close()`（main.py:1258-1259）完成**之前**杀进程——正是软路径要避免的数据丢失。

### 1.2 进程内大脑 + 瘦外肢的存活分割

**外肢 `anima/watchdog.py`（瘦身后，保留模块路径以维持 heartbeat.py:307 的 `from anima.watchdog import _update_heartbeat` 耦合）**只保留无人能替代的：

```python
def run_limb(dry_run: bool = False) -> None:
    while True:
        if not _singleton_guard.acquire():     # 🔨 PID/port 锁(折叠 fixer D2/F2,local.py 重复 spawn bug)
            _log("another ANIMA owns the lock — exiting"); return
        proc = subprocess.Popen([sys.executable, "-m", "anima"])   # watchdog.py:377 复用
        reason = _supervise(proc)               # 见下
        if reason == "stop":                    # ret==0 且无 marker → 真正停机
            return
        _persist_relaunch_count(reason)         # requested+crash 统一计入一个上限(折叠 A3)
        _cooldown(reason)                       # CRASH_COOLDOWN_S watchdog.py:46
```

`_supervise` 只做**三信号合议的活性检测**（折叠 layering E12）——绝不单靠 `/v1/status`：

1. `proc.poll()` 退出（watchdog.py:419）：区分 ret==0+无marker(stop) / 有效marker(requested) / 其他(crash)。
2. **hb-file age > 120s**（watchdog.py:43,462-474）——由 `script_hb` 专用**线程**写（heartbeat.py:304-308，**不在事件循环上**，故对负载免疫），是负载免疫的真相源。
3. **loopback-only `/v1/status` 不可达**——仅作**佐证**。

**僵死判定 = (hb-file stale ≥120s) AND (/v1/status 不可达)，且维持完整 3 次合议窗（≈3min）**。绝不单凭 `/v1/status` 硬杀（重负载下长 cognitive turn / GC / 120s LLM 级联会延迟 HTTP handler → 误杀健康进程）。

**Sentinel 自身活性由外肢监督（折叠 layering B4 / interfaces F3/F4）**——这是新设计引入而旧 watchdog 没有的 SPOF：

- Sentinel 写**自己的单调 tick token**（独立于 hb-file）。外肢检测：进程活（`/v1/status` 可达）但 Sentinel tick 冻结 N 间隔 → "进程活、Sentinel 死" → 强制硬重启。
- Sentinel 主循环体 `while True: try/except`，逐迭代存活（镜像 heartbeat 自保护，heartbeat.py:136），只有灾难性失败才杀循环。
- `on_report`/`repair`/`can_handle` **绝不外抛**：捕获后**以 CRITICAL 审计该捕获**并自增 self-error 计数，该计数本身成为 PROCESS 故障（折叠 F4——"never raises" 不等于静默吞掉）。

**PROCESS 域不在进程内 FSM（折叠 escalation A2）**：被重启的东西无法从内部观察自己的死亡。进程内 Sentinel **没有 PROCESS 修复挡位**；`restart_proc` 只是"向外肢发 marker 请求"这一动作，真正的 Popen 永远只在外肢。

### 1.3 模块布局

```
anima/guardian/                     🔨 build-new
  signal.py        # Signal, Severity, Component (闭合线格式)
  sentinel.py      # Sentinel: FSM, 升级策略, 关联抑制, 审计, self-tick
  policy.py        # 每域阈值/挡位梯/预算 (数据, 非代码; 来自不可写源)
  ledger.py        # 🔨 跨进程持久预算+DEFEATED 账本 (与外肢共享, 见 §3)
  watchdog/
    base.py        # Probe ABC (sample 异常隔离)
    task_probe.py  # add_done_callback → Signal (修 ZERO-callback 漏洞)
    llm_probe.py   # router.get_status() 只读快照
    db_probe.py    # db.status()
    mesh_probe.py  # gossip tick 活性 + split_brain.is_readonly
    channel_probe.py   # ⚠️ INFO-capped
    resource_probe.py
    log_pattern_probe.py  # 🔨 tail jsonl (非 anima.log), 补 typed probe 未覆盖的故障类
  fixer/
    base.py        # FixStrategy ABC + RepairResult
    registry.py    # 按 harshness 排序 + 冷却/预算/in-flight 去重
    llm_fix.py db_fix.py task_fix.py channel_fix.py process_fix.py code_repair_fix.py
anima/watchdog.py                   🔒 瘦身为外肢 (保留模块路径)
```

外肢**移除**：`_invoke_claude_code` 稳态路径、`_detect_error_pattern`（移入进程内 `LogPatternProbe`，读 jsonl 非 anima.log）、`_build_*_prompt`、`_post_startup_health_check`。**保留**：spawn + 三信号活性 + 服从 marker + 单一 startup-crash-loop `claude -p` 兜底（默认关，见 §4f）。

---

## 2. 接口与数据契约

### 2.1 线格式 `guardian/signal.py`

```python
class Severity(IntEnum):          # 有序, max()=最坏
    OK = 0; INFO = 1; WARN = 2; ERROR = 3; CRITICAL = 4

class Component(str, Enum):       # 闭合可监控子系统注册表
    LLM="llm"; DB="db"; TASK="task"; MESH="mesh"
    CHANNELS="channels"; RESOURCE="resource"; PROCESS="process"

class Health(str, Enum):
    OK="ok"; DEGRADED="degraded"; DOWN="down"
    RECOVERING="recovering"; UNKNOWN="unknown"
    # 折叠 interfaces G1: rollup 显式定义——UNKNOWN 按 DEGRADED-for-alerting 计,
    # 绝不当 OK(stale-as-OK 是最致命的假阴性)。serving={OK,DEGRADED,RECOVERING}。

@dataclass(frozen=True, slots=True)
class Signal:
    component: Component
    severity: Severity
    code: str                      # 稳定机器码 "llm.circuit_open"
    detail: str
    ts: float = field(default_factory=time.time)   # 仅供人读
    mono: float = field(default_factory=time.monotonic)  # 折叠 escalation G2: 区间用单调钟
    data: Mapping[str, object] = field(default_factory=dict)  # 原始 status 快照
    self_healed: bool = False      # True=子系统已自动切换,这是"汇报上行",仅预警不升级
    source: str = "probe"          # "probe"|"report-sink"|reporter 名
```

`self_healed=True` 即"子系统自身自动切换报上来"——DB 故障转移成功报 `severity=WARN, self_healed=True`，Sentinel 升预警但**不**升级到 Fixer，除非预警**持续**（DB 始终不恢复）。

⚠️ **折叠 escalation C2**：LLM 的"已故障转移到 local 且 local 在服务"(DEGRADED) 与"故障转移后 local 也在挂"(ANOMALY/ERROR) 必须靠 `LocalServerManager.is_running` + 实际成功服务信号区分；缺正面证据时**默认 ERROR**，否则全量 LLM 宕机被静默当成稳定降级。

### 2.2 核心数据契约（frozen，自描述）

```python
@dataclass(frozen=True, slots=True)
class HealthReport:               # probe 返回
    component: Component; health: Health
    ts: float = field(default_factory=time.time)
    pressure: float = 0.0         # 0..1, clamp; 折叠 G3: circuit 已开时语义="failing-open"非"更失败"
    detail: str = ""
    raw: Mapping[str, object] = field(default_factory=dict)  # 子系统原生快照,Sentinel 不解释
    source: str = "probe"
    def faulted(self) -> bool: return self.health in (Health.DOWN, Health.DEGRADED)

@dataclass(frozen=True, slots=True)
class Fault:
    id: str                       # 折叠 G2: 完整 uuid4(非 hex[:12]), 防审计外键碰撞
    component: Component; health: Health; severity: Severity
    summary: str; first_seen: float; last_seen: float
    occurrences: int = 1
    signature: str = ""           # 折叠 D1: 故障签名(component+code+异常指纹) 用于预算/去重
    state: str = "open"           # 折叠 D1: open→(N 健康报告迟滞)→closed, 闭合规则显式
    evidence: Sequence[HealthReport] = field(default_factory=tuple)

@dataclass(frozen=True, slots=True)
class Warning:                    # 预警: 任何修复之前升起
    id: str; fault_id: str; component: Component
    severity: Severity; message: str
    raised_at: float = field(default_factory=time.time)
    repeat_count: int = 1; escalated: bool = False

@dataclass(frozen=True, slots=True)
class RepairAction:
    id: str; fault_id: str; component: Component
    kind: "RepairKind"; reason: str; attempt: int = 1
    params: Mapping[str, object] = field(default_factory=dict)

class RepairKind(str, Enum):      # 闭合 Fixer 目录, 按 harshness 升序
    LLM_BACKSTOP="llm_backstop"; DB_RECOVER="db_recover"
    RESTART_TASK="restart_task"; RESTART_CHANNEL="restart_channel"
    RESTART_PROC="restart_proc"; CODE_REPAIR="code_repair"
    ALERT_ONLY="alert_only"       # 折叠 interfaces F1: 显式终态,非意外

class RepairOutcome(str, Enum):
    REPAIRED="repaired"; NOOP="noop"; INEFFECTIVE="ineffective"
    FAILED="failed"; REFUSED="refused"; ROLLED_BACK="rolled_back"
    DEFERRED="deferred"; ALERT_ONLY="alert_only"; ESCALATE="escalate"

@dataclass(frozen=True, slots=True)
class RepairResult:
    action_id: str; kind: RepairKind; outcome: RepairOutcome
    detail: str = ""; ts: float = field(default_factory=time.time)
    new_health: Health = Health.UNKNOWN     # 折叠 B2: 修复后绝不自称 OK, 最高 RECOVERING
    reversible: bool = False                 # 折叠 C2: 显式声明,代替撒谎的 no-op rollback
    rollback_token: object | None = None
    @property
    def ok(self) -> bool: return self.outcome in (RepairOutcome.REPAIRED, RepairOutcome.NOOP)

@dataclass(frozen=True, slots=True)
class AuditRecord:                # 一切(report→warn→repair→rollback) 单行 append-only
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    node_id: str = ""             # 折叠 splitbrain M3: 节点作用域,防 jsonl 外多节点碰撞
    component: Component | None = None
    phase: str = ""               # "report"|"warn"|"escalate"|"repair"|"rollback"|"recover"
    severity: Severity = Severity.INFO; message: str = ""
    fault_id: str | None = None; warning_id: str | None = None; action_id: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)
```

### 2.3 传感器 + report-sink

```python
class Probe(ABC):
    id: Component; interval_s: float
    @abstractmethod
    async def sample(self) -> list[Signal]:    # 折叠 G19: 必须异常隔离——抛出转
        ...                                     # Signal(ERROR,"probe.failed"),绝不传播杀循环
    async def aclose(self) -> None: ...

# PUSH: 子系统自愈即上报。折叠 interfaces A1 + fixer A1 的致命发现:
# router._degradation_callback 是单槽(router.py:77,88)且已被 _on_model_change(main.py:251)占用,
# 且只在 model-substitution 触发, 电路熔断 open(router.py:114-119)只 log 不回调。
# ⇒ 必须改 router 为多订阅 add_degradation_listener(), 保留 dashboard notifier,
#    并在 circuit-open 处(router.py:114)显式 emit; LLM 故障检测以 LlmProbe 轮询 get_status()
#    为主, listener 为辅(两路互补, 不漏事件)。
ReportSink = Callable[[Signal], None]
```

`TaskProbe` 是 push（修 ZERO add_done_callback / 任务死亡 UNOBSERVED）：

```python
class TaskProbe(Probe):
    id = Component.TASK
    def watch(self, task, *, kind: str) -> None:
        task.add_done_callback(partial(self._on_done, kind=kind))
    def _on_done(self, task, *, kind):
        # 折叠 B5: _shutting_down 在关停序列最顶设置, 之后 report() 硬丢全部信号
        if self._shutting_down or task.cancelled(): return
        exc = task.exception()
        if exc is None: return        # 折叠 fixer D3: 正常返回的任务绝不报 ANOMALY
        self._pending.append(Signal(Component.TASK, Severity.ERROR,
            code=f"task.died.{kind}", detail=repr(exc),
            data={"kind": kind, "exc_type": type(exc).__name__}))  # 异常签名用于 poison 检测
    async def sample(self): out, self._pending = self._pending, []; return out
```

### 2.4 Fixer Strategy（闭合，幂等，可逆/拒绝）

```python
class FixStrategy(ABC):
    kind: RepairKind; component: Component
    harshness: int          # 1 最轻 … 100 (RESTART_PROC=90, CODE_REPAIR=100)
    cooldown_s: float; max_attempts: int
    @abstractmethod
    async def can_handle(self, fault: Fault) -> bool: ...   # registry 包 try/except,抛出=不处理
    @abstractmethod
    async def repair(self, action: RepairAction) -> RepairResult:
        """必须幂等(已修复返 NOOP); 必须不抛(包成 FAILED)。"""
    async def rollback(self, result: RepairResult) -> RepairResult:
        # 折叠 C2: 默认拒绝, 由 reversible 字段而非撒谎 no-op 表达
        return replace(result, outcome=RepairOutcome.REFUSED, detail="irreversible")

class Registry:
    def register(self, reg: "Registration") -> None: ...
    def strategies_for(self, fault: Fault) -> tuple[FixStrategy, ...]:  # can_handle 匹配,harshness 升序
        ...
    # 折叠 G4: in-flight 集合按 fault.signature 去重——同故障重复上报不并发起两个 repair
    def claim(self, fault: Fault) -> bool: ...
    def release(self, fault: Fault) -> None: ...
```

### 2.5 Sentinel API + `/v1/status` 载荷

```python
class Sentinel:
    def __init__(self, *, policy: Policy, registry: Registry, ledger: Ledger,
                 event_queue, audit_sink: Callable[[AuditRecord], None],
                 restart_hook: Callable[[str], None]): ...
    def register(self, reg: Registration) -> None: ...
    def report(self, sig: Signal) -> None:        # push 入口(listener/self-heal)
        ...                                        # 折叠 F4: 永不静默吞;捕获→CRITICAL 审计
    async def run(self) -> None: ...               # 长生命任务(自 try/except 逐迭代存活)
    async def stop(self) -> None: ...              # 先置 _shutting_down 再排空(折叠 B5)
    def snapshot(self) -> "SentinelStatus": ...    # /v1/status (authed) + dashboard hub
    # restart_hook(reason): 写原子 marker + reload_manager.restart_requested + shutdown_event.set()
```

`/v1/status` 拆**两个端点（折叠 layering G20）**：
- **`/v1/healthz`（loopback-only，无鉴权，极简）**：`{"alive":true,"sentinel_tick":N,"draining":false}`——外肢的机机活性通道，绑回环不暴露。误配鉴权 → 永远 401 → 外肢误判不可达 → 杀进程风暴。故此端点不鉴权。
- **`/v1/status`（鉴权）**：富 `snapshot()` 给人/dashboard。替换 health.py:29 的静态 `{"status":"ok"}`；保留 `/v1/health` 静态供 k8s/LB。

```json
{ "overall":"degraded", "ts":1782900000.0, "sentinel_tick":48213,
  "active_faults":1, "active_warnings":2, "correlated_incident":null,
  "components":[
    {"component":"llm","health":"degraded","pressure":0.66,"open_warnings":1,
     "last_report_ts":1782899990.0,
     "last_repair":{"kind":"llm_backstop","outcome":"repaired","new_health":"degraded"}},
    {"component":"db","health":"ok","pressure":0.0,"open_warnings":0,"last_repair":null},
    {"component":"channels","health":"unknown","pressure":0.0,"open_warnings":1,
     "alert_only":true,"last_repair":null}
  ]}
```

---

## 3. 升级状态机

### 3.1 状态（每域一个独立 FSM 实例）

| 状态 | 含义 | 谁动手 |
|---|---|---|
| `OK` 正常 | 无异常, 计数衰减 | 无 |
| `WARNED` 预警 | 异常已见, 低于升级阈值。**只告警, 不修复** | Sentinel 发 SYSTEM_ALERT |
| `DEGRADED` 降级 | 子系统**自身**自动切换(LLM 级联/DB failover)并报上, 跑备份, 可接受 | 无(观察) |
| `REPAIRING` 修复中 | 挡位执行中 | Fixer |
| `RECOVERING` 恢复中 | 修复返回, 在恢复熔断器探针窗内观察 | Sentinel 观察 |
| `ESCALATED` 已升级 | 到达重启级, 严格最后手段, 双重门控 | Fixer(受预算门) |
| `DEFEATED` 失守 | 挡位耗尽/预算用尽。保持降级, 大声呼人, **停自动修复** | 仅人/agent |

`DEGRADED` vs `WARNED` 是"自愈即汇报"规则的关键：LLM 级联或 DB failover 自己触发 = **报告**，落 `DEGRADED`（稳定观察），**不**进 `REPAIRING`。只有降级运行**自身持续失败**才爬梯。

### 3.2 转移表

| From | Trigger | Guard | To | Action |
|---|---|---|---|---|
| OK | sig≥WARN(faulted) | count<warn_thr | WARNED | 发预警, 起窗计时 |
| OK | self_healed | — | DEGRADED | 记录, 发 info 告警 |
| WARNED | faulted | count≥warn_thr **且** 双信号确认 | REPAIRING | claim+起 1 挡, 设冷却 |
| WARNED | healthy | 窗内衰减到 0 | OK | 清 |
| WARNED | sustained>K 窗 | 折叠 escalation E2: 慢滴漏死区 | ESCALATED/ALERT | 防卡死 WARNED 永不升不降 |
| DEGRADED | faulted | 降级也在挂(双信号) | REPAIRING | 起 1 挡 |
| DEGRADED | self primary 回 | — | RECOVERING | 观察 |
| REPAIRING | REPAIR_OK | — | RECOVERING | new_health 最高 RECOVERING, 起 confirm 窗 |
| REPAIRING | REPAIR_FAIL | attempt<max | REPAIRING | 冷却后重试同挡 |
| REPAIRING | REPAIR_FAIL | attempt≥max, 下挡<重启级 | REPAIRING | 爬下一挡 |
| REPAIRING | REPAIR_FAIL | 下挡≥重启级 | ESCALATED | 门控全局预算(账本) |
| REPAIRING | **无 REPAIR_RESULT 超时** | 折叠 F1: confirm 窗×max 内无结果 | REPAIRING/ESCALATED | 当 FAIL 处理, 防卡死 REPAIRING |
| ESCALATED | budget OK | — | REPAIRING | 执行重启级(严格) |
| ESCALATED | budget 耗尽 | — | DEFEATED | 保持降级, 呼人 |
| RECOVERING | healthy ×(confirm 窗内 N 次, 无新故障) | — | OK | 全重置, 清计数 |
| RECOVERING | faulted | confirm 窗内 | REPAIRING | 恢复熔断器跳闸→**爬一挡**(不下滑) |
| any | top 挡 FAIL | — | DEFEATED | 停自动化, 呼人 |
| DEFEATED | 显式 reset() | 人/agent | OK | 仅此一出口 |

### 3.3 决策伪代码（核心）

```
def on_report(sig, now_mono):     # 折叠 G1: 全部 report 经单一 asyncio 队列串行消费(线程安全)
    if self._shutting_down: return None       # 折叠 B5: 关停最顶置位, 硬丢
    d, p = self.domains[sig.component], self.policy[sig.component]

    # 全局关联抑制(折叠 layering F17 / interfaces D3):
    if self._correlated_incident_active(now_mono):
        return self._alert_only("correlated incident — per-component actuators suppressed")

    if sig.self_healed:                        # 自愈=汇报, 非故障
        d.state = DEGRADED; return self._alert_info(sig)
    if sig.severity == Severity.OK:            # healthy tick
        d.window.decay(now_mono)
        if d.state == RECOVERING and d.confirm_clear(now_mono): d.full_reset(); d.state = OK
        elif d.state == WARNED and d.window.count == 0: d.state = OK
        return None

    # faulted-class:
    d.window.add(now_mono)
    if d.state in (OK, WARNED, DEGRADED):
        if d.window.count < p.warn_thr or not self._dual_signal_confirmed(d):
            d.state = WARNED
            return self._emit_warn(sig) if d.warn_cooldown_ok(now_mono) else None
        return self._engage(d, p, rung=p.first_repair_rung, now_mono)   # 升级
    if d.state == REPAIRING:
        if now_mono - d.last_attempt < p.rung_cooldown[d.rung]: return None   # 冷却,防抖
        if d.attempts < p.rung_max[d.rung]:
            d.attempts += 1; return self._action(d, d.rung, now_mono)
        nxt = d.next_rung(p)
        if nxt is None: d.state = DEFEATED; return self._action(d, ALERT_ONLY, now_mono)
        if nxt >= RUNG_RESTART:                # 重启级双重门:挡耗尽 AND 全局预算
            if not self._budget_ok_persistent(now_mono):   # 折叠 E3: 账本跨进程持久
                d.state = DEFEATED; self._page_human(); return self._action(d, ALERT_ONLY, now_mono)
            d.state = ESCALATED; self._budget_spend_persistent(now_mono)
        d.rung = nxt; d.attempts = 1; return self._action(d, nxt, now_mono)
    if d.state == RECOVERING:                  # 恢复期内复发→爬, 不重置挡
        return self._engage(d, p, rung=d.next_rung(p), now_mono)
    return None                                # ESCALATED/DEFEATED: 已到/过顶
```

### 3.4 不变量（无限循环禁绝）

1. **预警优先**：`OK→WARNED` 永远先于任何修复；修复仅在 `count≥warn_thr` **且双信号确认**后。
2. **自愈≠故障**：`self_healed` 路由到稳定 `DEGRADED`，绝不进梯。
3. **重启双门**：必须挡位耗尽 **AND** 通过**持久化全局预算账本**。
4. **DEFEATED 是陷阱态且跨进程持久**（折叠 escalation E3 + ledger）：仅显式 `reset()` 出。外肢重启进程后，新 Sentinel 启动**先读账本**——若某域 DEFEATED 则不自动修复，否则外肢每次重启都让 FSM 忘记失守、重爬全梯、再失守。
5. **恢复熔断器**：`RECOVERING→OK` 需 confirm 窗内持续健康（迟滞）；复发**爬升不下滑**；修复后 `new_health` 最高 `RECOVERING`，恢复仅由独立 `recovered` 报告确认，**不**因 Fixer 自称成功而重置升级计数（折叠 B2/F2——否则乐观 APPLIED 重置梯子→永久抖动）。
6. **预算按 component+signature 计**（折叠 D1），非按 fault.id——否则抖动者通过不断刷新 fault.id 或维持同 id 来逃预算。Flap 检测：M 次状态转移/T 秒 → 强制 `damped` 态只报不修。
7. **单调钟**（折叠 G2）：所有区间/冷却用 `time.monotonic()`，墙钟仅人读日志。
8. **跨进程单一预算账本**（折叠 escalation A1 / fixer D3）：`GLOBAL_REPAIR_BUDGET` 与外肢的 `MAX_CONSECUTIVE_FIXES` 是**同一个**文件账本（`.guardian/ledger.json`，含 PID+时间戳序列），两者动手前都查它。否则"镜像 MAX_CONSECUTIVE_FIXES=3"是谎言——会得到 6 次而非 3 次。外肢放弃后（watchdog.py:437-439 静默 `return` → 进程永久死）必须先经账本写 CRITICAL 呼人，非静默退出。

---

## 4. Fixer 修复策略目录

闭合契约：Sentinel 只经 `can_handle/repair/rollback` 触达子系统。每条 `RepairResult` 追加到 `guardian_actions.jsonl`（复用 JSONFormatter，utils/logging.py）。梯序（harshness）保证重启(90)、代码修复(100) 仅在低挡可逆挡返 INEFFECTIVE 后到达。

### (a) `LlmBackstopFixer` ✅ 观察+保底（绝不动熔断器）

```python
class LlmBackstopFixer(FixStrategy):
    kind = RepairKind.LLM_BACKSTOP; component = Component.LLM
    harshness = 10; max_attempts = 3
    async def can_handle(self, fault):
        st = self._router.get_status()         # 只读 router.py:502
        # 折叠 fixer F2: 窄化——circuit_open 本身是 router 在正确工作, 不是可动作故障。
        # 仅当 circuit_open 持续 > probe_interval×N **且** local backstop 也不健康才动手。
        return st["circuit_open"] and self._stuck_beyond(st) and not self._local.is_running
    async def repair(self, action):
        # 1) 只观察, 绝不写 _consecutive_failures(router.py:112 探针污染同一计数→跳闸)
        # 2) 确保级联底部 local 可达, 让 router 自身 tier1→…→local(router.py:99-137)有落点
        ok = await self._local.ensure_running(timeout=30)   # 必须 PID/port 锁幂等(折叠 D2/F2)
        # 3) 不调任何 reset——router 自身 30s 半开探针自愈(router.py:121-137)比 Sentinel 强
        if not ok:
            return RepairResult(action.id, self.kind, RepairOutcome.INEFFECTIVE,
                "local backstop unavailable", new_health=Health.DOWN)
        return RepairResult(action.id, self.kind, RepairOutcome.REPAIRED,
            "ensured local LLM backstop; deferring to router auto-reset",
            new_health=Health.RECOVERING, reversible=True)   # 折叠 B2: 不自称 OK
```

**为何不 force-demote/reset**：router 无公共 force/demote/reset 入口；唯一杠杆是真探针调用，喂同一 `_consecutive_failures`（router.py:112-113）可跳闸——严禁。`force_tier` 若将来加，必须带 TTL/lease 自动过期（折叠 B3：否则 router 想恢复 tier1 而 Sentinel 钉死在 tier2 → 自锁降级）。**结论：LLM 这里 Sentinel 的正确角色是观察+保底+报，active 力度远低于初稿所承认的。**

🔨 前置：`add_degradation_listener()` 多订阅（保留 dashboard），circuit-open 处显式 emit（router.py:114）。

### (b) `DbRecoverFixer` ✅ 仅经 PgSync 同锁 recover_now（confirm-only 倾向）

```python
class DbRecoverFixer(FixStrategy):
    kind = RepairKind.DB_RECOVER; component = Component.DB
    harshness = 20; max_attempts = 5         # 幂等→可慷慨重试
    async def can_handle(self, fault):
        # 折叠 fixer F1: using_local 是设计安全的降级态, 不是故障。
        # 仅当 local 持续超预期窗 **且** primary 重新可达才可动作。
        st = await self._db.status()
        if not st["using_local"]: return False
        if not await self._db.primary_reachable(): return False   # 🔨 旁路探针,不碰 _conn
        # 折叠 splitbrain D2: split-brain 期间绝不 failback(可能 replay 分叉本地写)
        if self._split_brain.is_readonly: return False
        return self._local_persisted_beyond_window(st)
    async def repair(self, action):
        # 折叠 fixer B3/G1 + splitbrain C3: 绝不直调 _replay/switch_to_primary;
        # 调 PgSyncManager.recover_now() —— 它在 PgSync 自己的锁内串行(与 300s _loop 互斥)
        res = await self._sync.recover_now()    # 🔨 见下, 同一把锁
        if res.get("recovered"):
            return RepairResult(action.id, self.kind, RepairOutcome.REPAIRED,
                f"replayed+failback, lww_overwrites={res.get('lww',0)}",
                new_health=Health.RECOVERING, reversible=False)
        if res.get("reason") == "in_progress":
            return RepairResult(action.id, self.kind, RepairOutcome.NOOP, "sync loop owns it")
        return RepairResult(action.id, self.kind, RepairOutcome.INEFFECTIVE,
            f"stay local (writes safe): {res.get('reason')}", new_health=Health.DEGRADED)
```

**红队结论折叠**：`DB_RECOVER` 实质应**倾向 confirm-only**——PgSync 的 300s `_loop`（pg_sync.py:91-96）本就是单一 failback owner。Fixer 的角色是**加速**（在 primary 重新可达时不必等满 300s）而非第二个驱动者。`recover_now` 复用 `_loop` 完全相同的体（replay-then-switch），继承 failback 排序不变量。关键安全护栏全部保留：
- **绝不直调 `switch_to_primary()`**（pg_db.py:153 绕过 replay → 丢/重本地写）。
- **单锁串行**：`recover_now` 与 `_loop` 经同一 `PgSyncManager` 锁互斥（折叠 C3）；持有时 `recover_now` 立即返 `in_progress` 非阻塞。
- **failback 排序不变量**：`_using_local` 仅可在 `switch_to_primary` 内 True→False，且仅在 `_replay_local_to_primary` 同 pass 返 True 后到达（pg_sync.py:94-95，已正确）。
- **迟滞**（折叠 splitbrain C4）：primary 须**连续 N 探针/稳定窗**可达才允许切，切后**failback 冷却** M 秒——防 Neon autosuspend 抖动引发 failback 风暴。

🔨 前置接口（§5 详述）：`pg_db.status()` / `pg_db.primary_reachable()`（旁路探针，不碰 `_conn`、不翻 `_using_local`）；`PgSyncManager.recover_now()`（同锁）；`static_knowledge` 合并修复（见 §5 Part 1）。

### (c) `TaskRestartFixer` ⚠️ 须 restart() 契约, 否则 alert-only

```python
class TaskRestartFixer(FixStrategy):
    kind = RepairKind.RESTART_TASK; component = Component.TASK
    harshness = 40; max_attempts = 3
    async def can_handle(self, fault):
        spec = self._sup.spec_for(fault.evidence.get("kind"))
        # 折叠 layering B6 + fixer D3: 朴素重建会泄漏旧子循环+双注册(两个 script_hb 写 hb-file,
        # 两个 cognitive 双消费 event_queue, gossip 双发布)——严格比一个死任务更糟。
        # 仅当该任务有显式审计过的 restart() 契约才可处理; 否则 DEFER→process_fix。
        return spec is not None and spec.has_restart_contract
    async def repair(self, action):
        spec = self._sup.spec_for(action.params["kind"])
        if not spec.has_restart_contract:
            return RepairResult(action.id, self.kind, RepairOutcome.DEFERRED,
                f"{spec.kind} no idempotent restart() — escalate to process restart")
        # 折叠 fixer D3 poison 检测: 同任务同异常签名死 N 次→不重建(否则死循环)
        if self._sup.is_poison(spec.kind, action.params.get("exc_type")):
            return RepairResult(action.id, self.kind, RepairOutcome.DEFERRED,
                "poison signature — quarantine head event, escalate")
        await spec.restart()        # 契约: 取消+await 子循环, 拆注册, 干净重初始化
        # 折叠 fixer D4: heartbeat 重启须先立即写 hb-file 时间戳再 spin-up, 防外肢 stale 误杀
        return RepairResult(action.id, self.kind, RepairOutcome.REPAIRED,
            f"restarted {spec.kind}", new_health=Health.RECOVERING, reversible=True)
```

**契约要求（折叠 B6）**：每个可重启任务需显式 `restart()`：(a) 取消+await 自己的子循环，(b) 拆注册（TaskProbe、hb-file 写者），(c) 干净重初始化。`heartbeat` 拥有 3 子循环 + 写 hb-file（heartbeat.py:304），其 `restart()` 重建全部并立即写一次 hb-file 时间戳。`cognitive` 携 `reload_manager` + 消费 `event_queue`，**无幂等 restart 契约前标 `has_restart_contract=False`**，本挡 DEFER 到 process_fix（干净全重启比半接线安全）。**Poison 处理**：死任务必须先隔离/跳过杀它的 event（dead-letter 队头）再重建，否则跨层无限重启（task→proc→boot 重读持久 poison→再崩）。

### (d) `ChannelRestartFixer` ⚠️ alert-only（底层是 no-op bug，先修 bug）

```python
class ChannelRestartFixer(FixStrategy):
    kind = RepairKind.RESTART_CHANNEL; component = Component.CHANNELS
    harshness = 50; max_attempts = 0       # 折叠 layering E13 + fixer A3: 默认 alert-only
    async def can_handle(self, fault):
        # hub._active_channels 永不赋值(总空, 真 bug); is_connected 是 stale bool;
        # 无 connect/disconnect; send() 失败被吞。无真相源可读 ⇒ 本挡不可动作。
        return False   # 直到前置 bug 修复
```

`ChannelProbe` **INFO-capped、绝不驱动升级**（折叠 E13：否则空 `_active_channels` 永报全宕→CRITICAL→process_fix 重启风暴在装饰性 bug 上）。🔨 前置 bug（修完才解锁）：(1) 修 `hub._active_channels` 赋值；(2) 加 `Channel.connect/disconnect` + 真实 `is_connected`（传输驱动）；(3) `send()` 失败上报 `channels.down` 而非吞。`can_handle` 经 registry try/except 包裹，缺方法不抛（折叠 fixer A3）。

### (e) `ProcessRestartFixer` 🔒 外肢, 严格最后手段, 不可逆

```python
class ProcessRestartFixer(FixStrategy):
    kind = RepairKind.RESTART_PROC; component = Component.PROCESS
    harshness = 90; max_attempts = 2       # 极严; 超出→呼人
    async def can_handle(self, fault):
        # 折叠 interfaces C3: 绝非"通用兜底"。须双信号佐证(hb-file stale AND ≥N 独立故障报告)
        return self._dual_corroborated(fault)
    async def repair(self, action):
        # 折叠 fixer A2: 不存在 watchdog 轮询的 restart 文件——这是 net-new。
        # 真路径: 写原子带-PID-epoch marker(phase=draining) → 优雅退出 → 非零'请重启'码
        #         → 外肢读有效 marker 才重启(ret==0 会让外肢停机, 见 §1.1)。
        ok = self._write_restart_marker(reason=action.reason, fault_id=action.fault_id)
        if not ok:
            return RepairResult(action.id, self.kind, RepairOutcome.ESCALATE,
                "marker write failed — cannot hand off safely")
        # 折叠 fixer B2: 退出前先延长 hb-file/写"shutting-down"标记, 外肢据此延长 grace,
        # 防 checkpoint 写到一半被 hb-stale 误杀。
        self._mark_draining_for_limb()
        self._request_restart(action.reason)   # reload_manager.restart_requested + shutdown_event.set()
        return RepairResult(action.id, self.kind, RepairOutcome.REPAIRED,
            "requested external-limb restart (atomic marker → graceful → relaunch)",
            new_health=Health.UNKNOWN, reversible=False)
    async def rollback(self, result):
        return replace(result, outcome=RepairOutcome.REFUSED, detail="process restart irreversible")
```

### (f) `CodeSelfRepairFixer` ⚠️ 经 evolution 管线, 安全-by-construction, 绝不碰自愈 TCB

```python
# 折叠 layering F15/F16 + interfaces E1/E2/E4 + fixer B1/E1/E2/E3/E4:
# 自修复 TCB 用 ALLOW-LIST(白名单), 不用 deny-list(deny 会漏)。
_SELF_REPAIRABLE_ROOTS = frozenset({  # 仅这些目录可被自动代码修复触及; 其余一律 ALERT_ONLY
    "anima/cognitive/", "anima/channels/", "anima/api/handlers/",
    # 显式排除(即使将来加目录也绝不含): anima/guardian/, anima/watchdog.py,
    # anima/llm/router.py 熔断逻辑, anima/memory/pg_db.py & pg_sync.py failback,
    # anima/evolution/{engine,deployer,reload}.py, anima/network/split_brain.py,
    # main.py 任务接线, tests/ 与 test runner。
})

class CodeSelfRepairFixer(FixStrategy):
    kind = RepairKind.CODE_REPAIR; component = Component.PROCESS  # "any persistent"
    harshness = 100; max_attempts = 1      # 每故障签名硬上限/6h 长窗
    async def can_handle(self, fault):
        files = fault.evidence.get("files", [])
        if not files: return False         # 无可隔离故障点 → ALERT_ONLY
        if not all(any(f.startswith(r) for r in _SELF_REPAIRABLE_ROOTS) for f in files):
            return False                   # 触及 TCB/核心/测试 → ALERT_ONLY(须人确认)
        # 折叠 E2: 用于 consensus 的 mesh/LLM 自身须 OK(别在降级脑上跑共识)
        if not self._consensus_substrate_healthy(): return False
        # 折叠 E2/fixer E2: 故障须 ≥2 独立信号复现(probe AND report-source 一致),
        # 绝不靠单一 UNKNOWN/stale probe 或可伪造日志(server.py:298 /debug 注入)
        return self._reproduced_by_dual_independent_signals(fault)
    async def repair(self, action):
        sha = self._record_good_sha()      # 折叠 F15/E3: 部署前记录 last-known-good
        prop = make_proposal(type="bugfix", files=action.params["files"],
                             problem=action.reason, risk="high", human_confirmed=False)
        # 经 evolution 管线: governance 核心模块门(anima/core/*,main.py 须人确认) →
        # consensus 投票 → git worktree 隔离 → TestRunner(跑 known-good 修订的测试套,
        # 禁改测试文件, 折叠 E4) → _review_diff → cherry-pick 部署 → ReloadManager 热重载
        status = await self._evo.submit_proposal(prop)
        if status not in ("approved_executing", "queued"):
            return RepairResult(action.id, self.kind, RepairOutcome.ALERT_ONLY,
                f"self-repair not admitted: {status}")
        self._arm_probation(sha, action.params["files"])   # 见 rollback
        return RepairResult(action.id, self.kind, RepairOutcome.REPAIRED,
            f"entered reviewed pipeline (sha={sha})", new_health=Health.RECOVERING, reversible=True)
    async def rollback(self, result):
        # 折叠 E3: 进程内自动回滚假设有活 Python——若坏部署卡死循环则跑不了。
        # 故 post-reload 健康检查 + git revert 由外肢拥有(它在子死后存活),
        # 用部署前写的 last-known-good SHA marker。进程内回滚仅 best-effort, 非唯一安全网。
        return replace(result, outcome=RepairOutcome.ROLLED_BACK, detail="external-limb reverts to good SHA")
```

**与现状的关键和解（折叠 layering F16 + fixer B1）**：watchdog 今天**已经**在 error-pattern 上跑 `claude -p` 改活树并让 hot-reload 接手（watchdog.py:484-491）——这是第二个修复大脑，会与 evolution 管线对同一文件并发写、git index 争用、双热重载。**新设计禁止之**：稳态代码修复**唯一**走 evolution 管线（Fixer 挡 5）；watchdog 的 `_detect_error_pattern→claude -p` 稳态分支**移除**，error-pattern 检测降为进程内 `LogPatternProbe`（读 jsonl）上报 Sentinel。`claude -p` **只**作为外肢的 **startup-crash-loop 兜底**保留（进程连 Sentinel 都启动不了时），且：
- **默认关闭**（最危险时刻——无 Sentinel、无稳定测试环境——跑盲改）。
- 跑前 `_record_good_sha`；修+重启后仍在探针窗内崩 → `git reset --hard` 回快照再放弃。
- **绝不 `git add -A`**（折叠 fixer B2 + 项目记忆"灵魂提交勿 push"）——仅 add traceback 涉及文件；soul/data 目录入 commit-guard denylist；`--allowedTools` 去掉 `Write`、`Edit`/`Bash` 限路径白名单。
- 上限 **1 次**（非 3），因每次都未被 Sentinel 观察。

**所有 alert-only 兜底**：触及 TCB/核心/测试文件、无可隔离故障点、consensus 基质降级、单信号/可伪造来源 → 一律 `ALERT_ONLY`（可*提议*，须人确认）。

---

## 5. 脑裂与数据合并

两个共享"split-brain"一词的物理不同问题，必须分清。

### Part 1 — DB local↔cloud 失联（隔离→重连→合并）

用户字面场景。**~90% 已就绪。**

**已就绪 ✅**：失联（`_run_locked` 捕 `OperationalError/InterfaceError` → `_connect` 试 primary 后 local → 置权威 `_using_local`，pg_db.py:138-151,64,106）；重连合并（`PgSyncManager._loop` 每 300s 当 `using_local` 时 `_replay_local_to_primary`→`_reconcile`：每表 `MAX(ts)` watermark、拉 `>=watermark`、`INSERT…ON CONFLICT(pk) DO NOTHING`，pg_sync.py:85-101,162-196）；**failback 排序不变量已正确**（`switch_to_primary` 门控于 `recovered=replay()==True`，pg_sync.py:94-95）。

**真正的正确性缺口（红队揭露，必须修，否则初稿的"修复"本身是 bug）🔨**：

- **缺口 A — `static_knowledge` 是唯一非 append-only 同步表，两个写路径都是无时间戳守卫的 LWW**：live upsert 无条件 clobber（pg_store.py:363-369），replay 用 `ON CONFLICT DO NOTHING`（pg_sync.py:189-190）→ 失联期间用户**本地编辑的** `static_knowledge` 行在 failback 时被**静默丢弃**。**修复必须用版本计数器而非墙钟**（折叠 splitbrain C1 致命发现）：`updated_at` 是客户端 `time.time()` DOUBLE PRECISION（pg_store.py:369、pg_schema.sql:58），多台物理机两把**不同步墙钟**。`WHERE dst.ts < EXCLUDED.ts` 的 LWW 会因时钟漂移**确定性丢失正确的编辑**（本地钟慢→新编辑 ts 反而小→丢；本地钟快→陈旧值 clobber 新值）。故：
  - 加 `version BIGINT`（每次 upsert 单调自增），合并按 `(version, ts 仅作 tiebreak)`。
  - **每次丢弃非相同值的 LWW 覆盖必须 journal**（旧值+新值+双 ts+双 node_id）到 `guardian_actions.jsonl`（node 作用域 ID，非 DB 行——否则审计自己被合并的 `ON CONFLICT DO NOTHING` 吃掉，折叠 M3）。仅计数不保值="audited"是谎言。
  - **删除须 tombstone**（折叠 C2）：`delete_static_knowledge` 是硬 DELETE（pg_store.py:381），replay 会把被删行**复活**。改软删 `deleted_at` 并纳入同步集，按同版本规则合并。

- **缺口 B — Sentinel 看不见 DB 健康，无法加速恢复 🔨**：加两个闭合接口：
  ```python
  # pg_db.py 旁路探针, 绝不 mutate _conn / 翻 _using_local:
  async def status(self) -> dict: ...          # {open, serving, using_local, primary_dsn_set,...}
  async def primary_reachable(self, timeout_s=5) -> bool: ...  # SELECT 1 on throwaway conn
  # pg_sync.py 同锁按需触发, 复用 _loop 体, 串行于 300s tick:
  async def recover_now(self) -> dict: ...     # {recovered, reason, lww, in_progress}
  ```

**Sentinel 观察/驱动**：Watchdog `DbProbe` 轮询 `db.status()`；`using_local` 翻 True → Sentinel 升**预警**（非修复——failover 是系统正确自愈）→ 周期 `primary_reachable()` → True 且过稳定窗且非 split-brain → 升级到 `recover_now()`。**重启永不是 DB failover 的响应。**

### Part 2 — node-mesh 脑裂（gossip 分区, 共享 DB）

物理不同：多 ANIMA 节点 gossip。分区使各侧都自认多数，**都继续写同一 Postgres**——并发权威导致的腐坏。**检测存在；执行是死标志。这是唯一真正不安全的缺口。**

✅ **检测**：`SplitBrainDetector.check(alive_count)` 算多数（`is_majority`: `visible>total/2`），少数侧置 `_readonly=True`（split_brain.py:20-42, node.py:123-128），愈合清。每 60s 从 `_periodic_network_tasks` 驱动（main.py:692-696）。

⚠️🔨 **执行：不存在**。`is_readonly` 除测试外**无人读**；`check()` 返回值在 main.py:696 **被丢弃**；无写路径查它。**少数节点照样自由写共享 primary。栅栏是装饰品。**

**修复 🔨 — 在单一 DB 写瓶颈处栅栏**（不散落各调用点）：
```python
# pg_db.py 可注入谓词, 默认放行:
def set_write_fence(self, predicate: Callable[[], bool]) -> None: ...
# _run_locked 内, is_write 工作在取游标前:
#   if is_write and self._write_fence and not self._write_fence(): <fenced 路径>
# write_sync/write_many_sync 传 is_write=True; fetch_* 保持 False(读总放行)
```

**关键修正——栅栏绝不抛异常杀循环（折叠 splitbrain H2）**：`WriteFenced` 深在 `to_thread` 里抛、无 `add_done_callback` 观察、会杀掉未包 try/except 的 cognitive 循环——比它修的 bug 更糟（lobotomize 节点）。故 **fenced 写不是异常，而是被吞+记录+计量的结果**：fenced 的共享权威写**转入本地 spool/fallback DB**，按 Part-1 replay 路径在解栅时重放（把"被栅栏"当"被隔离"处理，复用已验证的合并）。节点**自身本地记账**（含"为何 fenced"）**不受栅栏**——否则连解栅决策都写不了会卡死。

**栅栏反应延迟（折叠 H1）**：`check()` 每 60s 跑——分区后最多 60s 栅栏才闭。改由 gossip 探测器**状态变更回调**（phi-accrual alive→suspect→dead）驱动 `check()`，按检测延迟反应；保留 60s 扫描作 backstop。

**quorum 分母信任问题（折叠 H4/H5）**：`get_alive_count()=1+alive peers`，`is_majority` 用持久 `registered_nodes`（node.json）作 total。新启节点 roster 只含自己 → 永不栅栏即使隔离；或死节点未过期（1h）→ 把幽灵算活 → 误栅自己达 1h。**多数分母必须用 gossip 观测到的活成员，非持久文件**；栅栏门控于"我**正面看到**多数消失"，非"静态 roster 说我小"。

**解栅与协调（折叠 H3/H5）**：分离 `_readonly_partition` 与 `_readonly_manual`，栅栏=两者 OR，`check()` 只清 partition 位（否则维护栅栏被分区逻辑静默掀掉）。gossip 线程死的**误栅**：给 gossip 加**活性 tick**（`while self._running` 内更新时间戳，gossip.py:396），Sentinel 误栅触发键于 **stale gossip tick**（非 peer count——死线程会留陈旧 peers 使"peers==0"永不触发）。误栅 → Fixer **重启 gossip 任务**，**绝不自动解栅**（那会毁安全属性——修传感器，非掀栅栏）。Sentinel 子线程修复与外肢全进程重启经共享"repair-in-progress"marker 协调，外肢给 grace 窗。

### Part 2 愈合时的合并

少数侧**全程只读 → 未产生分叉写 → 无需合并**（这正是栅栏 vs Part-1 合并的全部意义）。多数侧写已是共享 DB 权威。对比 Part 1：隔离节点*确实*本地写过，*必须*合并。

### 数据安全不变量（一句话给评审）

- **Part 1**：`_using_local` 仅可在 `switch_to_primary` 内 True→False，且仅在同 pass `_replay_local_to_primary` 返 True 后到达；`switch_to_primary` 保持约定私有，绝不从 app 代码直调；`recover_now` 与 `_loop` 同锁互斥。
- **Part 2**：少数侧（gossip 活成员计的少数）写共享 primary 必须被 `_run_locked` 写栅栏拦下（转本地 spool）；2/2 偶分**双侧都栅栏**（正确，宁停勿腐）→ 硬预警呼运营，**绝不自动选赢家**。

### Sentinel 闭合信号词汇表

| Signal | 来源 | 含义 | Sentinel 响应 |
|---|---|---|---|
| `db.failover` | `db.status().using_local` | Part1: primary 不可达, 服 local | 预警; `primary_reachable`+稳定窗 → Fixer `recover_now()` |
| `db.lww_overwrite` | `recover_now` lww>0 | Part1: 合并覆盖了行(已 journal 旧值) | audit-only |
| `mesh.fenced` | `split_brain.is_readonly` True | Part2: 本节点少数, 共享写已转 spool | 预警; 若 gossip tick stale → 疑误栅 → Fixer 重启 gossip |
| `mesh.even_split` | gossip 活成员 2 而见 1 | Part2: 双侧栅栏 | ⚠️ 硬预警, 升运营, 绝不自动解 |
| `mesh.detector_dead` | gossip tick 冻结 | 探测器死(沉默≠健康) | Fixer 重启 gossip 任务, 绝不自动解栅 |

---

## 6. 日志安全检测

**独立 `security_events.jsonl` sink，绝不喂 watchdog 代码修复器。** 今天失败登录 / 429 / path-traversal / tool-BLOCKED / content-blocks **零日志**（仅 anima.log 午夜轮转 backupCount=1）。

**设计**：
- 新增结构化安全事件，经 `JSONFormatter` 写**专用 `security_events.jsonl`**（区别于 `guardian_actions.jsonl` 与 anima.log）。信号：`auth.failed`、`rate.limited`、`path.traversal`、`tool.blocked`、`content.blocked`。
- **隔离铁律（折叠多处）**：安全事件 sink **绝不**成为 Sentinel `Signal` 来源，**绝不**喂 watchdog 的 error-pattern/代码修复器。攻击者经 `/debug` 日志注入（server.py:298）可伪造 anima.log 行——若任何检测器 grep anima.log 文本（旧 `_detect_error_pattern` 正是如此），攻击者伪造 `[ERROR]` 5×/300s 即可远程触发 `claude -p` 代码改写或进程重启（折叠 escalation H2 / fixer F3——RCE-邻近的监督者控制）。
- **检测器只消费结构化、认证信号**：`LogPatternProbe` tail **jsonl**（机器字段、不可被请求处理器伪造），绝不 grep anima.log 自由文本。Sentinel 审计行**绝不**以 ERROR/WARNING 级别落入 anima.log（否则 Sentinel 自己的日志喂 watchdog error-rate → 自维持重启循环，折叠 interfaces A2）——走独立 logger，watchdog grep 明确排除 guardian-origin 行。
- **锁定设计**：安全事件**只**驱动告警/限流/锁定（如连续 auth.failed → 临时封 IP），**永不**驱动代码或进程修复挡位。先修 `/debug` 注入（server.py:298 输入消毒）再让任何日志衍生检测驱动任何动作。

---

## 7. 可观测 & 审计 & 配置

**`/v1/status`（鉴权，富）**：`sentinel.snapshot()` 后端，替换 health.py:29 静态 `{"status":"ok"}`；含 `overall`/`sentinel_tick`/`active_faults`/`correlated_incident`/每域 `{health,pressure,open_warnings,last_repair,alert_only}`。**`/v1/healthz`（loopback,无鉴权,极简）**给外肢机机活性。`/v1/health` 静态保留给 LB。

**Dashboard 字段**：`snapshot()` 注入 hub 快照（hub.py:154-179）；预警经 `SYSTEM_ALERT`（复用 300s 冷却，heartbeat.py:213-227）——但 **`DEFEATED` 与预算耗尽以独立、不受 300s 冷却抑制的严重度呼人**（折叠 H1/G4：终态告警不是 warn；surfacing 节奏须 ≥ 升级节奏，否则 `open_warnings` 在 /v1/status 爬升而无 SYSTEM_ALERT 触发——人面通道被静默限流到内部升级率之下）。

**审计 `guardian_actions.jsonl`**：每条 `Signal` 摄入、每次状态转移、每条 `RepairResult`、每次 LWW 覆盖（含旧值）单行 append-only（node 作用域 UUID）。可重构每次合并/升级/修复。`/debug` 注入**不是** Signal 源——Sentinel 只摄入注册 probe/listener 的 typed `Signal`，伪造日志行驱动不了升级。

**Guardian 配置块**（每组件 enable/dry_run/阈值，来自不可写源，evolution 管线不可改）：
```toml
[guardian]
enabled = true
[guardian.ledger] path = ".guardian/ledger.json"; budget_max = 3; budget_window_s = 3600
[guardian.llm]      enable = true;  dry_run = false; warn_thr = 4; rung_cooldown_s = 30
[guardian.db]       enable = true;  dry_run = false; failback_cooldown_s = 120; stable_probes = 4
[guardian.task]     enable = true;  dry_run = false; poison_threshold = 3
[guardian.channels] enable = false; dry_run = true   # alert-only 直到 hub bug 修
[guardian.mesh]     enable = true;  dry_run = false   # 栅栏=spool; 修复=alert-only
[guardian.process]  enable = true;  dry_run = false; allow_restart = true;  max_restarts = 2
[guardian.code]     enable = false; dry_run = true;  allow_code_repair = false  # 默认关,最受护
[guardian.limb]     startup_claude_p = false          # 默认关(最危险最后兜底)
```

---

## 8. 分阶段落地

每阶段以 flag 门控，被动→主动，agent-self-repair 最后且最受护。**每阶段都真启动一次 app 验证（项目铁律）。**

- **P0 契约 + 被动观察（零动作）**：建 `anima/guardian/` 包（signal/contracts/sentinel 骨架/probe ABC）；接 `TaskProbe.add_done_callback`（修 ZERO-callback 漏洞——最高价值、纯观察）；`LlmProbe`/`DbProbe`（需 `db.status()`/`primary_reachable()`）只读轮询；`/v1/status` + `/v1/healthz`。全 `dry_run=true`，只写审计、只升预警、零修复。**验证**：启动 app，杀一个 long-lived 任务（注入异常），确认 `task.died` 出现在 `/v1/status` 与 `guardian_actions.jsonl`，且 app 不崩。
- **P1 跨进程账本 + 外肢瘦身 + 软重启协议**：建 `ledger.json`（Sentinel+外肢共享预算/DEFEATED）；外肢瘦身为三信号合议 + marker 协议（原子/PID/epoch，非零'请重启'码）+ Sentinel self-tick 监督；移除外肢稳态 `claude -p` 与 log-grep。**验证**：触发软重启 marker，确认外肢优雅重启（非停机）、排空窗内不误杀；冻结 Sentinel tick，确认外肢硬重启。
- **P2 安全可逆修复主动化**：开 `llm_backstop`（观察+ensure_local，PID 锁幂等）、`db_recover`（`recover_now` 同锁 + 迟滞 + 非 split-brain 门）。`static_knowledge` 版本计数器合并 + tombstone + LWW journal。**验证**：断 Neon，确认 failover→预警；恢复 Neon，确认 recover_now 加速且不抖动、本地编辑不丢、删除不复活。
- **P3 写栅栏 + task restart 契约**：`set_write_fence` + `is_write` 门 + spool-on-fence（非抛异常）；gossip 活性 tick + 回调驱动 `check()`；为 heartbeat/terminal 写 `restart()` 契约（cognitive 暂 DEFER）。**验证**：模拟少数分区，确认共享写转 spool 且 cognitive 循环不死；愈合后 spool 重放。
- **P4 进程重启主动化**：开 `restart_proc`（双信号佐证 + marker 握手 + 预算门 + draining 标记防误杀）。**验证**：制造确认的 PROCESS 故障，确认严格重启、预算耗尽进 DEFEATED 持久化跨重启。
- **P5（最后，最受护）agent code self-repair**：`code_repair` 经 evolution 管线，**白名单** TCB、双独立信号复现、consensus 基质健康门、part-of-the-pipeline 测试不可改、外肢拥有 last-known-good SHA 回滚。外肢 startup `claude -p` 仍默认关。**验证**：注入白名单内目录的可复现缺陷，确认走 proposal→consensus→test→review→reload；注入 TCB 文件缺陷，确认 ALERT_ONLY 不动手。

---

## 9. 残余风险 & 需用户拍板的决策

**残余真实危险**：
1. **两控制器协调靠共享文件账本/marker**——文件系统竞态、孤儿 marker、账本损坏仍是攻击面（已用原子写/PID/epoch/归档缓解，但非分布式共识强度）。极端：外肢与 Sentinel 对账本的并发读改写在罕见时序下仍可能各花一次预算。
2. **`static_knowledge` 版本计数器**解决跨机时钟问题，但**并发编辑仍是 LWW**——真正并发的两个编辑，败者被丢（已 journal 旧值可人工恢复，但非自动合并）。单灵魂/低并发下可接受，多活节点高并发下需 CRDT 级方案。
3. **gossip 活成员 quorum** 依赖 gossip 自身正确——若 gossip 误报成员（非线程死而是逻辑 bug），栅栏决策仍可能错；活性 tick 只抓"线程死"不抓"逻辑错"。
4. **evolution 管线的 consensus 在降级脑上的质量**——P5 要求基质健康才跑，但"健康"判断本身可能被部分降级污染。
5. **外肢 startup `claude -p`**（即便默认关）一旦开启，是全系统最高风险执行器：进程启动不了时盲改、无 Sentinel 观察、`git reset` 回滚依赖 git 本身健康。

**需用户拍板的决策**：
1. **DB 自动 failback 的激进度**：保持 confirm-only（仅 PgSync 300s `_loop` 驱动，Sentinel 只观察）更安全但恢复慢达 5min；开 `recover_now` 加速更快但增竞态面。**默认建议 confirm-only + 仅在 primary 稳定窗确认后加速。** 你要哪档？
2. **进程重启预算**：`max_restarts=2/window` 后进 DEFEATED 呼人——对生产是否够紧？还是要求**任何**进程重启都先呼人确认（半自动）？
3. **agent code self-repair 是否启用**：白名单内目录是否真允许**全自动**部署（经 evolution 管线），还是即便管线全绿也**强制人确认**才 cherry-pick？（P5 默认 `allow_code_repair=false`。）
4. **外肢 startup `claude -p` 兜底**：保留（默认关）作为进程连启动都不行时的最后稻草，还是**彻底删除**（项目已有 evolution 管线，盲改启动不了的进程风险极高）？**建议删除。**
5. **2/2 偶分裂的仲裁**：双侧栅栏=写全停（安全但可用性归零）。是否要配置一个**静态 tiebreak 节点 ID**（指定某节点在偶分裂时仍可写），以可用性换一点安全余量？还是坚持"宁停勿腐"全人工介入？

---

**关键文件**：新包 `D:\data\code\github\anima\anima\guardian\`（signal/contracts/sentinel/policy/ledger + watchdog/* + fixer/*）；瘦身 `D:\data\code\github\anima\anima\watchdog.py`（外肢，证实 ret==0 停机于 watchdog.py:425-427，error-pattern claude -p 于 484-491）；`anima\llm\router.py`（单槽 callback router.py:77,88 须改多订阅，circuit-open 仅 log 于 114-119，get_status 502）；`anima\memory\pg_db.py`（using_local:105/138，switch_to_primary:153）+ `pg_sync.py`（_loop replay-then-switch:91-96，reconcile MAX(ts)+DO NOTHING:177-190）+ `pg_store.py`（static_knowledge LWW:363-369，硬 DELETE:381）+ `pg_schema.sql`（updated_at DOUBLE:58 须加 version/deleted_at）；`anima\network\split_brain.py`（is_readonly 死标志:17-47）+ `node.py`（is_majority:123-128）+ `gossip.py`（alive_count:261-263 须加活性 tick）；`anima\core\heartbeat.py`（hb-file 写:304-308，SYSTEM_ALERT:213-227）；`anima\main.py`（任务接线:1143-1147，shutdown:1208-1265，restart 循环:1284）；`anima\api\health.py`（静态:29）；`anima\api\server.py`（/debug 注入:298）。
