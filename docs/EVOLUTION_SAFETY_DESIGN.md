# ANIMA 自进化 & 安全系统设计

> 目标：让 Eva 能成长（学技能、装插件，必要时改自己的核心），但**任何一次进化或自修复都不能把自己改死**。
> 本文是蓝图；实现分阶段，每阶段真启动验证。基于 2026-06 深度审查（见 `CODE_REVIEW_2026-06.md`）。

## 0. 三条不可违背的原则

1. **冻结恢复核（Frozen Recovery Core）** — 一组明确的模块，**进化和自修复 Fixer 永远不能改**。
   它包含"判断能不能进化"和"坏了怎么恢复"的所有代码。只有人直接编辑源码才能改它们。
   *理由*：若进化能改 governance 门 / 看门狗 / 回滚逻辑本身，一切保证归零（审查 P0-1 正是这个洞）。
2. **恢复优先级**：`重启 → 回滚到 known-good → （最后手段）自修复 → DEFEATED 停机喊人`。
   回滚永远优先于"让一个可能已降级的 LLM 在着火时写新补丁"。自修复走与普通进化**完全相同**的沙盒+测试门，
   没有救火绿色通道。反复失败 → 进入 DEFEATED 终态（停机比死循环安全）。
3. **外置恢复**：坏进化之后进程内逻辑不可信，所以"撤销坏进化"的最终执行者必须在进化进程**之外**
   （看门狗 + 启动自检），且它们自己属于冻结核。

## 1. 分层成长模型

| 层 | 内容 | 撤销方式 | 门控 | 失败影响面 |
|---|---|---|---|---|
| **L0 加性** | 装技能/插件/工具 | 翻开关（禁用单元） | 能力 manifest + 默认拒绝 + 资源/速率限制 | 仅该单元自身的调用失败 |
| **L1 软进化** | 人格/提示/记忆/配置（范围内调参） | 改数据 | 范围白名单 + 冷却 | 行为漂移，不崩溃 |
| **L2 硬进化** | 改自己的源码（非冻结模块） | git 回滚到 known-good | 真人类审批 + 真沙盒 + 真测试 + 模块白名单 | 可能崩溃 → 启动自检兜底 |
| **冻结核** | 守护/看门狗/恢复/沙盒/测试/governance/部署/main 重启循环 | 仅人工 | 任何自动路径都不能碰 | — |

**L0 是默认成长路径。** 关键安全前提：每个技能 manifest 声明所需能力（工具集、可读写路径、网络主机），
运行时**默认拒绝 + 强制隔离**。没有这个，"装插件"等于"换壳的任意代码执行"（审查 P0-2）。

### 冻结核清单（`anima/guardian/frozen.py` 强制）
```
anima/guardian/**          # 守护大脑 + Fixer + ledger + handoff + frozen 自身
anima/watchdog.py          # 外肢
anima/core/reload.py       # 重载/checkpoint
anima/core/governance.py   # 进化门本身
anima/core/boot_health.py  # 启动自检 + 已知良好锚 + 自动回退
anima/evolution/**         # 进化引擎/沙盒/部署（进化不能改进化）
anima/main.py              # 启动 + 重启循环
anima/__main__.py
```

## 2. 恢复链（外置 + 分级）

```
组件故障 (Sentinel 探测)
  → 安全可逆修复 (L0 Fixer: LLM backstop / DB failover)    [已有]
  → 仍坏 → 优雅进程重启 (budget-gated)                      [P4, 已有但需修]
  → 重启后启动自检失败 → 自动回退到 known-good 提交         [新, 本期]
  → 反复失败超预算 → DEFEATED：停机 + 喊人                  [需修：看门狗当前忽略 DEFEATED]
  → (最后手段, 后续阶段) 自修复：生成补丁 → 同一沙盒+测试门 → known-good 兜底
```

### Known-good 锚 + 启动自检（`anima/core/boot_health.py`，本期实现）
- **记录**：每次启动成功通过自检后，把当前 commit SHA 写入 `data/.guardian/known_good.json`。
- **自检**：子系统起来后跑快速 smoke（healthz 可达、cognitive task 活、memory 可达、llm router 已配）。
- **自动回退**：若本次启动**紧跟一次进化重载**（存在 evolution checkpoint）且自检失败 →
  `git reset --hard <known_good_sha>` → 以"请求重启"退出码退出，让外层重新拉起干净代码。
  *普通冷启动失败不回退*（可能是环境问题，不是坏进化）。

### 看门狗去致盲（本期修，审查 P0-4）
当前：guardian 重启走**进程内重载、不退出进程** → `restart.marker` 永不被 `consume_restart_marker` 消费
（它只在真实退出时调用）→ `draining` 恒真 → 存活监督被永久关闭。
修法（两道）：
1. **marker 老化**：看门狗只在 marker 年龄 < `RESTART_MARKER_MAX_AGE_S`(120s) 时视为 draining；
   过期 marker 不再致盲，于是卡死的进程内重载仍会被存活判定抓到。
2. **启动时消费**：新一轮 `run()` 启动健康后调用 `consume_restart_marker()`，正常路径下 marker 只存活一次重启窗口。

### 重启刹车（本期修，审查 core P2-2）
`main_entry` 的进化重启循环加滑动窗口计数：N 次快速重启（如 600s 内 5 次）→ 停止自旋 + 记录，
不再无上限 `sleep(2)` 重试。看门狗侧已有预算，但需补：**看门狗在 relaunch 前检查 DEFEATED**（审查 guardian P1）。

## 3. 真人类门（本期修，审查 P0-1）

当前 `governance.check_evolution_proposal` 信任 `proposal["human_confirmed"]`——由 Eva 自己填。
修法：
- **冻结模块**：命中 `frozen.is_frozen()` → **硬拒绝，无任何 override**。
- **核心模块**（非冻结但敏感，如 stages/pipeline）：需要**带外审批**——
  人工通过 CLI/API 在 `data/.guardian/approvals/` 写入针对该 proposal 的审批令牌；
  从 LLM 可见的工具 schema 中删除 `human_confirmed`，服务端默认 False。
- 普通模块：正常走沙盒+测试门。

## 4. 真沙盒 + 真测试门（后续阶段，L2/自修复前置）

当前是假的（审查）：沙盒只是共享 `.git` 的同机 worktree；level-3 起的第二个 ANIMA 共享 Neon DB + 密钥；
baseline pytest 崩溃/超时（失败计数 0）被当通过。目标：
- **Bash 保留（设计决定）**：ANIMA 本就要做系统级操作，Eva 的通用 `shell` 工具（`tools/builtin/shell.py`，
  带 `safe_subprocess` 风险分级）和进化实现 agent 都**保留 Bash**。对**自改代码**的约束不放在"砍工具"，
  而放在**审查层**——diff 必须过 governance（冻结核硬拒绝 + 核心模块人工审批）+ 测试门 + review + 部署/自动回退门
  才能落地。这就是审查 P0-2 的处置方式：控制在评审，不在能力。
- **完整隔离**（容器/VM、无主机网络、独立 DB、不可推送的 .git）：仅在将来需要运行**不可信**代码时才上，排后续。
- **测试门**：区分"0 失败"与"没跑起来"；collection 错误/超时按硬失败处理。
- **部署修复**（审查 P0-3）：安全标签用真实 pre-deploy SHA（非 `HEAD~1`）；分支名集中一处（停止 push master / rollback push private 不一致）；
  `stash pop` 冲突有 abort 路径；自主进程绝不 `--force` 推共享分支。

## 5. 实施顺序（每步真启动验证）

- **第一期（地基）✅ 已完成**（commit 1582469）：① 实现 agent 去 Bash + 擦密钥 ② 冻结核注册表 +
  governance 硬拒绝 + 真核心审批 ③ 看门狗去致盲（marker 老化 + 启动消费）④ known-good 锚 +
  启动自检 + 进化后自动回退 ⑤ 重启刹车 + 看门狗查 DEFEATED。
- **第二期 ✅ 已完成**：真测试门（区分"0 失败"与"没跑起来"——崩溃/超时/collection 错误按硬失败）；
  部署/回滚修复（安全标签用真实 HEAD、分支名统一 `evolution.git_branch`、stash pop 冲突有 abort 路径、
  回滚不再 `--force` 推共享分支）；限流真拦截 + 拒绝计入冷却；worktree add 前 prune；
  **level-3 沙盒默认关闭**（`evolution.sandbox_level3_enabled: false`——现实现会起共享 DB/密钥且可自进化的第二个脑，
  待隔离 DB profile 后再开）。
- **第三期（部分完成 ✅）**：L0 能力 manifest + 权限模型。已做：`anima/skills/permissions.py`
  —技能 `_meta.json` 声明 `permissions`(shell/network/filesystem/risk)；运行时**默认拒绝密钥环境**
  （`build_skill_env` 剥离全部 ANIMA 密钥，闭合"装技能=读全部密钥"）；**安装审批门**（远程源/高权限/未声明
  权限的技能需带外审批 `data/.guardian/skill_approvals/<name>.approved`，本地低权限可装）；工具风险按 manifest。
  待做：network/filesystem 的**硬隔离**（容器/命名空间），现仅为声明+审批依据。
- **第四期**：L2 硬进化收窄（模块白名单）+ 真隔离 level-3 沙盒（独立进程/DB/不可推送 .git）+ 技能 network/fs 硬隔离。
- **第五期**：自修复 Fixer（最后，约束最重，复用沙盒+测试门，回滚兜底，DEFEATED 终态）。
