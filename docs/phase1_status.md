# Phase 1 Status Report

> 截至 2026-03-15 07:00 — Phase 1 实施状态

---

## 项目规模

| 指标 | 数值 |
|------|------|
| Python 文件 | 95 |
| 代码行数 | ~11,000 |
| 内置工具 | 22 |
| 测试 | 110 pass, 2 skipped |
| 活跃节点 | 2（台式机 + 笔记本） |

---

## 已完成功能

### Phase 0 核心（完成）

| 功能 | 状态 | 验证 |
|------|------|------|
| AgenticLoop（LLM 原生循环） | ✅ | 多轮工具调用、自然对话 |
| 规则引擎（零成本事件处理） | ✅ | 问候/文件变化/系统告警 |
| 22 个内置工具 | ✅ | shell, read/write/edit, glob, grep, web_fetch, remote_exec, spawn_agent, schedule_job 等 |
| 心跳引擎（15s/5min/1h） | ✅ | 系统采样、diff 检测、情感衰减 |
| 持久记忆（SQLite） | ✅ | 聊天、使用量、审计、快照 |
| 情感系统（4D + 衰减） | ✅ | engagement/confidence/curiosity/concern |
| Cron 调度器 | ✅ | 持久化到 data/scheduler.json |
| Skill 加载器（兼容 OpenClaw） | ✅ | _meta.json 格式 |
| OAuth 自动发现 | ✅ | ~/.claude/.credentials.json |
| Dashboard（4页 SPA） | ✅ | Overview/Chat/Usage/Settings |
| 可插拔人格（Eva） | ✅ | agents/eva/soul.md |

### Phase 1a：网络通信（完成）

| 功能 | 状态 | 验证 |
|------|------|------|
| Gossip 协议（ZMQ PUB/SUB） | ✅ | 两台物理机 6s 内互相发现 |
| 节点身份持久化 | ✅ | data/node.json |
| Phi Accrual 故障检测 | ✅ | 自适应阈值 |
| mDNS 零配置发现 | ✅ | zeroconf 库 |
| HMAC 消息签名 | ✅ | 防篡改 |
| 统一 5s Gossip 定时器 | ✅ | 心跳+状态交换+故障检测合一 |

### Phase 1b：会话路由 + 渠道（完成）

| 功能 | 状态 | 验证 |
|------|------|------|
| 会话分布式锁 | ✅ | 确定性冲突解决（低 ID 胜） |
| Discord 渠道 | ✅ | eva#3258 收发消息 |
| Webhook 渠道 | ✅ | HTTP POST 接收 |
| Discord → 事件队列 → 认知 → 回复 Discord | ✅ | 完整链路验证 |

### Phase 1c：记忆同步 + 脑裂（完成）

| 功能 | 状态 | 验证 |
|------|------|------|
| 增量记忆同步（Lamport clock） | ✅ | sync_seq + 内容哈希去重 |
| 同步水位线持久化 | ✅ | data/sync_watermarks.json |
| 脑裂检测（多数派/少数派） | ✅ | 注册节点列表判定 |
| 少数派只读模式 | ✅ | 测试验证 |

### Phase 1d：Dashboard + 部署（完成）

| 功能 | 状态 | 验证 |
|------|------|------|
| Network 页面（拓扑+节点表） | ✅ | 路由已修复 |
| 移动端适配（600px 断点） | ✅ | 响应式 CSS |
| Dashboard 绑定 0.0.0.0 | ✅ | 非 localhost |
| Peer Dashboard 链接 | ✅ | 可点击跳转 |

### Phase 1e：繁殖（完成）

| 功能 | 状态 | 验证 |
|------|------|------|
| Spawn 打包（tar.gz） | ✅ | 94KB，74 Python 文件 |
| SSH 远程部署 | ✅ | paramiko |
| 本地部署（测试用） | ✅ | 指定目录 |
| CLI: `python -m anima spawn` | ✅ | --pack-only/--local/user@host |
| bootstrap.sh / .ps1 | ✅ | 一键启动脚本 |

---

## Phase 1 未完成功能

### 高优先级（影响核心体验）

#### 1. 节点间任务委派（Task Dispatch）

**现状**：`remote_exec` 是"远程遥控"——台式机 Eva 通过 SSH 在笔记本上执行命令，跳过了笔记本的 Eva。笔记本的 Eva 完全不知道发生了什么。

**应该是**：台式机 Eva 通过 gossip 事件总线发送 `TASK_DELEGATE` 事件 → 笔记本 Eva 的认知循环接收 → 笔记本 Eva 用自己的工具执行 → 结果通过 gossip 返回给台式机 Eva。

**需要**：
- 新事件类型 `TASK_DELEGATE` 和 `TASK_RESULT`
- `delegate_task` 工具（LLM 可调用）
- 接收端认知循环处理委派任务
- 结果回传机制
- 预计工作量：1-2 周

#### 2. 自修复能力

**现状**：工具失败时 Eva 会报告"shell 不工作"但不会尝试自己修复。

**应该是**：检测到工具连续失败 → 自动诊断（检查进程、端口、依赖） → 尝试修复（重启模块、重装依赖） → 报告结果。

**需要**：
- 工具健康检查机制
- 自修复策略库（L1-L4 分级修复）
- 连续失败计数器 + 自动触发
- 预计工作量：1 周

#### 3. 热重载完善

**现状**：`python -m anima --watch` 文件监控重启 + Dashboard 重启按钮。但自进化需要更完善的方案。

**应该是**：
- 代码更新后自动重启（不丢失会话状态）
- 配置变更热生效（不需要重启）
- 更新前自动备份
- 更新失败自动回滚

**需要**：
- 状态序列化/恢复
- 配置热加载 watcher
- 版本快照 + 回滚
- 预计工作量：1 周

### 中优先级

#### 4. 笔记本 SYSTEM_ALERT 风暴

**现状**：笔记本日志每 15s 触发一次 SYSTEM_ALERT + Rule Engine 处理。这是因为 diff 阈值在笔记本上触发太频繁。

**需要**：调整笔记本的 diff 阈值，或给 SYSTEM_ALERT 加冷却时间（同一类告警 5 分钟内不重复）。

#### 5. Gossip "Node alive" 日志风暴

**现状**：笔记本每 5s 打一条 `Node alive: DESKTOP-OTD1JE1`。应该只在状态变化时打日志，不是每次心跳都打。

**需要**：只在节点首次出现或从 dead/suspect 恢复时打 info 日志，其他时候 debug 级别。

#### 6. 共享看板

**现状**：每个节点有自己的看板。打开台式机看板看不到笔记本的详细信息（只有 Network 页的简要状态）。

**应该是**：一个看板显示所有节点的完整数据（心跳、工具使用、活动流、聊天历史）。

**需要**：看板数据通过 gossip 广播，或看板 WebSocket 代理到其他节点。

### 低优先级

#### 7. Discord 会话接管

**现状**：只有台式机运行 Discord bot。台式机关闭后笔记本不会自动接管。

**需要**：Discord token 在多个节点间协调——主节点运行 bot，主节点死亡后备用节点启动 bot。

#### 8. 滚动更新协议（ARP）

**设计已完成**（docs/phase1_distributed_network.md），但代码未实现。

#### 9. 向量时钟冲突解决

**设计已完成**，但目前只用了简单的 LWW（Last-Write-Wins）。

---

## 当前运行状态

```
Desktop (DESKTOP-OTD1JE1)
  IP: 192.168.1.153
  Node: anima-desktop-otd1je1-c3eba5ac
  Gossip: 9420 | Sync: 9422 | Dashboard: 8420
  Discord: eva#3258 ✅
  Model: Tier1=Opus 4.6, Tier2=Sonnet 4.6

Laptop (ZERON_X)
  IP: 192.168.1.159 (Tailscale: 100.109.112.90)
  Node: anima-spawn-1de968f7
  Gossip: 9420 | Sync: 9422 | Dashboard: 8420
  Discord: disabled (only main node runs bot)
  Model: Tier1=Opus 4.6, Tier2=Sonnet 4.6

Network:
  Gossip: ✅ 双向心跳（5s）
  Memory sync: ✅ 每 60s 增量同步
  Session routing: ✅ 分布式锁
  Split-brain: ✅ 多数派检测
```

---

## 明天继续的优先顺序

1. **节点间任务委派**（最重要——分布式的核心意义）
2. **日志风暴修复**（SYSTEM_ALERT 冷却 + Gossip alive 降级）
3. **自修复能力**（工具健康检查 + 自动修复）
4. **热重载完善**（状态保持重启）

---

*Phase 1 核心网络基座已建成。两台物理机器通过 Gossip 连接、通过 Memory Sync 同步、通过 Discord 与用户交互。剩余功能集中在"节点间协作"和"系统韧性"两个方向。*
