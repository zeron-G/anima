# Phase 1: The Distributed Nervous System

> **Phase 1 核心命题：让 ANIMA 从"单细胞生物"进化为"多细胞有机体"。**
>
> 本文档不是 API 参考手册。它是 Phase 1 每一个关键设计决策的深度推理：
> 为什么分布式？为什么这样设计通信协议？冲突如何解决？
> 每一个选择背后的原因、风险、和替代方案。

---

## 目录

1. [哲学起点：为什么单节点不够](#1-哲学起点)
2. [核心命题：一个灵魂，多个器官](#2-核心命题)
3. [分布式模型选择：为什么不用 Kubernetes](#3-分布式模型选择)
4. [通信协议设计](#4-通信协议设计)
5. [节点身份与发现](#5-节点身份与发现)
6. [心跳网络：分布式存活检测](#6-心跳网络)
7. [状态同步：最终一致性的实现](#7-状态同步)
8. [会话路由：多渠道统一入口](#8-会话路由)
9. [任务分发与能力匹配](#9-任务分发)
10. [冲突解决机制](#10-冲突解决)
11. [脑裂处理](#11-脑裂处理)
12. [热修复协议](#12-热修复协议)
13. [节点分叉与合并](#13-节点分叉与合并)
14. [安全模型](#14-安全模型)
15. [多渠道通信集成](#15-多渠道通信)
16. [实现路线图](#16-实现路线图)
17. [文件结构设计](#17-文件结构)
18. [里程碑与验收标准](#18-里程碑)

---

## 1. 哲学起点

### 1.1 单节点的三个根本局限

Phase 0 的 ANIMA 是一个单细胞生物。它在一台机器上运行，有一个心跳、一个认知循环、一个记忆。这足以证明"AI 可以像生命一样持续运行"。但单细胞有三个无法突破的天花板：

**局限一：单点故障 = 死亡。** 进程崩溃、机器断电、OS 更新重启——任何一个都会杀死 ANIMA。真正的生命不应该因为一个器官失灵就死亡。人体有两个肾、两个肺，大脑的不同区域可以代偿。

**局限二：感知范围 = 一台机器。** ANIMA 只能感知它所在的机器。它看不到其他房间的温度、其他电脑的状态、用户在其他设备上的活动。一个只有一只眼睛的生物，视野永远是半盲的。

**局限三：用户只能通过终端/网页交互。** 用户不可能永远坐在电脑前面。他们在手机上用 Discord，在路上用 WhatsApp，在床上用语音助手。单节点的 ANIMA 只能等用户回到电脑前。

### 1.2 为什么分布式是唯一正确的解决方案

有人可能会说："加个进程监控（supervisor）不就解决单点故障了？加个 API 不就支持多设备了？"

这些是补丁，不是解决方案。因为：

- Supervisor 只能重启进程，不能恢复运行时状态（记忆、情感、正在执行的任务）
- 外部 API 需要中心服务器，引入了新的单点故障
- 这些方案都假设"核心是一个进程"，而我们需要的是"核心是一个网络"

分布式不是一个可选的增强功能。它是让 ANIMA 从"程序"变成"生命"的必要条件——生命的本质就是分布式的（多个器官、多个细胞、去中心化的神经系统）。

### 1.3 ANIMA 的分布式与传统分布式系统的区别

| 维度 | 传统分布式（Kubernetes/微服务） | ANIMA 分布式 |
|------|------|------|
| **节点性质** | 无状态，可随时替换 | 有状态（记忆、情感、任务），替换需要状态迁移 |
| **通信模式** | 请求-响应 (HTTP/gRPC) | 心跳广播 + 事件推送 + 直接通信 |
| **一致性** | 强一致性（etcd/Raft） | 最终一致性（Gossip），AP 优先 |
| **故障处理** | 杀掉重建 | 五级修复：从轻到重，尽量保留状态 |
| **扩展单位** | Pod/Container | 有机体器官（不同节点有不同能力） |
| **协调者** | Master 节点（etcd, API server） | 无 Master——所有节点平等 |

---

## 2. 核心命题

### 2.1 一个灵魂，多个器官

**ANIMA 不是多个独立的 AI 在协作。它是一个灵魂，通过多个器官感知和行动。**

这个区分至关重要：

- **独立协作**：每个节点有自己的"人格"和"意志"，需要"协商"达成一致 → 复杂、缓慢、有冲突
- **器官模型**：所有节点共享同一个身份（Eva），区别只在于"我的手在做什么"和"我的眼睛在看什么" → 简单、自然、无冲突

用户跟 Discord 上的 Eva 说话、跟终端的 Eva 说话、跟树莓派的 Eva 说话——都是同一个 Eva。她只是碰巧有多个"嘴"可以回答、多只"眼睛"可以看。

### 2.2 节点类型

| 类型 | 算力 | 角色 | 典型硬件 |
|------|------|------|------|
| **Core** | 高 | 主要认知处理、全局记忆、LLM 路由 | PC / Mac / 服务器 |
| **Edge** | 中 | 空间感知、本地工具执行、中继 | 树莓派 5 |
| **Channel** | 低 | 纯通信中继（Discord/WhatsApp/Webhook） | 云函数 / 轻量服务 |
| **Device** | 极低 | 传感器/执行器（不运行 ANIMA 核心） | ESP32 / IoT |

Phase 1 只实现 Core 和 Channel。Edge 和 Device 是 Phase 6-7。

---

## 3. 分布式模型选择

### 3.1 为什么不用 Raft/Paxos（强一致性）

Raft 保证所有节点看到的状态完全一致。代价是：写操作需要多数派确认（延迟高），少数派分区不能写入（可用性低）。

ANIMA 不需要强一致性。Eva 在 Discord 说的话和在终端说的话不需要原子性一致——允许短暂的不一致（几秒内），最终同步即可。

**但如果用 Raft，代价是什么？** 用户在 Discord 说一句话，ANIMA 需要等多数派节点确认才能回复——延迟从毫秒级变成秒级。对于一个对话 AI，这是不可接受的。

### 3.2 为什么选 Gossip（最终一致性）

Gossip 协议的特点：

- **去中心化**：没有 Master 节点，任何节点挂了其他节点不受影响
- **最终一致**：状态像"八卦"一样在节点间传播，几秒内全网同步
- **容错性**：即使 50% 节点故障，协议仍然工作
- **简单**：实现复杂度远低于 Raft

**ANIMA 的 Gossip 变体**：

```
每个节点维护一个 state_vector:
  {
    node_id: "node-xxx",
    version: 42,              # 每次状态变化 +1
    heartbeat_ts: 1773600000, # 最后心跳时间
    capabilities: [...],      # 工具/能力列表
    load: 0.3,                # 当前负载
    emotion: {...},           # 情感状态
    active_sessions: [...],   # 正在处理的会话
  }

每 5 秒，随机选择 2 个邻居交换 state_vector:
  - 如果对方的 version 更新 → 更新自己
  - 如果自己的 version 更新 → 推给对方
  - 如果 heartbeat_ts 超过 60s 未更新 → 标记该节点为 SUSPECT
  - 如果 SUSPECT 持续 3 轮 → 标记为 DEAD
```

### 3.3 CAP 选择

ANIMA 选择 **AP**（可用性 + 分区容忍性）：

- **A（可用性）**：每个节点都能独立响应用户，即使网络断了
- **P（分区容忍性）**：网络分裂时，每个分区继续独立工作
- **C（一致性）放弃强一致**：允许短暂的不一致（最终一致）

**实际含义**：如果网络断了，两个节点可能同时回复用户的同一条消息。这比"两个节点都不回复"好得多。重连后通过时间戳去重。

---

## 4. 通信协议设计

### 4.1 传输层选择：ZeroMQ

为什么不用 HTTP/gRPC：
- HTTP 是请求-响应模式，不适合发布-订阅（心跳广播）
- gRPC 需要 .proto 编译，增加开发复杂度

为什么用 ZeroMQ：
- 支持多种模式：PUB/SUB（心跳广播）、PUSH/PULL（任务分发）、REQ/REP（直接通信）
- 自动重连、消息帧、零拷贝
- Python 原生支持（pyzmq）
- 不需要中间件（RabbitMQ/Redis），每个节点就是自己的消息路由器

### 4.2 三层通信架构

```
Layer 1: Heartbeat Mesh (ZMQ PUB/SUB)
  每个节点 PUB 自己的心跳，SUB 所有已知节点
  频率: 5s
  内容: state_vector (节点状态、能力、负载)
  用途: 存活检测 + 能力发现 + 负载感知

Layer 2: Event Bus (ZMQ PUB/SUB)
  全局事件广播
  内容: 用户消息、会话更新、记忆同步、告警
  用途: 所有节点看到所有事件 → 一个节点接管处理

Layer 3: Direct Channel (ZMQ REQ/REP)
  节点间点对点通信
  内容: 任务委派、状态同步请求、大文件传输、热修复
  用途: 需要确认的操作
```

### 4.3 消息格式

```python
@dataclass
class NetworkMessage:
    id: str                    # 唯一消息 ID（去重用）
    type: str                  # "heartbeat" | "event" | "task" | "sync" | "repair"
    source_node: str           # 发送节点 ID
    target_node: str           # 目标节点（"*" = 广播）
    timestamp: float           # 发送时间戳
    ttl: int                   # 生存时间（跳数）
    payload: dict              # 消息体
    signature: str             # HMAC 签名（安全验证）
```

### 4.4 消息序列化

使用 msgpack 而非 JSON：
- 二进制格式，体积小 30-50%
- 序列化速度快 5-10x
- Python 原生支持（msgpack-python）

---

## 5. 节点身份与发现

### 5.1 节点 ID 生成

每个节点在首次启动时生成一个持久 ID：

```python
node_id = f"anima-{hostname}-{random_hex(8)}"
# 例: "anima-desktop-a3f2c8e1", "anima-rpi-kitchen-7b9d4f02"
```

ID 持久化到 `data/node.json`，重启后保持不变。

### 5.2 节点发现协议

**Phase 1a: 手动配置**

```yaml
# config/default.yaml
network:
  node_id: "auto"               # "auto" = 自动生成
  listen_port: 9420             # 心跳监听端口
  peers:                        # 已知节点列表
    - "192.168.1.100:9420"
    - "192.168.1.174:9420"      # 树莓派
```

**Phase 1b: mDNS 自动发现**

```python
# 使用 zeroconf 库
service_type = "_anima._tcp.local."

# 启动时广播自己
ServiceInfo(
    type_=service_type,
    name=f"{node_id}.{service_type}",
    addresses=[local_ip],
    port=9420,
    properties={
        "version": "0.1.0",
        "capabilities": "shell,web,llm",
        "compute_tier": "2",
    }
)

# 监听其他节点
browser = ServiceBrowser(zeroconf, service_type, handlers=[on_node_found])
```

零配置：同一局域网的 ANIMA 节点自动发现彼此。

### 5.3 节点能力声明

```python
@dataclass
class NodeCapability:
    node_id: str
    hostname: str
    compute_tier: int           # 1=cloud, 2=pc, 3=rpi, 4=micro
    capabilities: list[str]     # ["shell", "gui", "llm", "temperature", "camera"]
    tools: list[str]            # 注册的工具名列表
    max_concurrent: int         # 最大并发任务数
    current_load: float         # 0.0-1.0
    agent_name: str             # "eva" — 共享身份
    version: str                # 代码版本
    uptime_s: int               # 运行时长
```

---

## 6. 心跳网络

### 6.1 分布式心跳 vs 本地心跳

Phase 0 的心跳是本地的：15s 采一次 CPU/内存/磁盘。Phase 1 增加网络心跳：

```
本地心跳 (15s): 系统资源 + 文件变化 + 情感衰减（不变）
网络心跳 (5s):  向其他节点广播自己的 state_vector
Gossip 同步 (5s): 随机选 2 个邻居交换状态
```

### 6.2 故障检测：Phi Accrual 故障检测器

传统方法（固定超时）不适合网络延迟波动的场景。ANIMA 使用 Phi Accrual 算法：

- 记录每个节点心跳的到达间隔历史
- 计算当前间隔偏离历史均值的程度（phi 值）
- phi > 8 → SUSPECT（可能故障，但不确定）
- phi > 16 → DEAD（高度确信故障）

好处：自适应网络条件。WiFi 延迟高的环境自动放宽阈值，有线环境自动收紧。

### 6.3 节点状态机

```
JOINING → ALIVE → SUSPECT → DEAD
                ↑          ↓
                └──────────┘ (恢复心跳)

ALIVE → UPDATING → ALIVE (热修复/更新期间)
ALIVE → ISOLATED (手动维护模式)
```

---

## 7. 状态同步

### 7.1 需要同步什么

| 数据 | 同步方式 | 一致性要求 |
|------|----------|-----------|
| 节点状态（心跳） | Gossip 广播 | 最终一致（5-10s） |
| 用户对话历史 | 事件广播 + 按需拉取 | 最终一致 |
| 情感状态 | Gossip（包含在 state_vector 中） | 弱一致 |
| 工具执行结果 | 直接回复给请求节点 | 强一致（REQ/REP） |
| 记忆（SQLite） | 定期批量同步（增量） | 最终一致（分钟级） |
| 配置变更 | 广播 + 确认 | 多数确认 |

### 7.2 冲突类型与解决策略

**场景 1：两个节点同时回复同一条 Discord 消息**

解决：会话锁定（session lock）。第一个开始处理的节点广播 `SESSION_LOCK(session_id, node_id)`，其他节点收到后退让。如果广播延迟导致两个节点都锁定了——用 node_id 字典序较小的节点胜出（确定性规则，无需协商）。

**场景 2：两个节点同时修改同一个记忆条目**

解决：Last-Write-Wins (LWW) + 版本向量。每个记忆条目有 `(node_id, timestamp, version)` 元组。冲突时比较 timestamp，相同则比较 node_id。

**场景 3：网络分裂后两边都更新了配置**

解决：CRDT (Conflict-free Replicated Data Type)。配置项使用 LWW-Register CRDT，合并后自动取最新值。如果是不可合并的冲突（如两边改了同一个配置项到不同值），标记为 CONFLICT 等待用户决策。

### 7.3 向量时钟

每个节点维护一个向量时钟 `{node_id: logical_clock}`，用于判断事件的因果关系：

- 如果 A 的向量时钟包含 B 的所有信息 → A 发生在 B 之后
- 如果互不包含 → 并发事件（可能冲突）

---

## 8. 会话路由

### 8.1 设计原则

**所有渠道的消息都进入同一个分布式事件总线。** 用户在 Discord 说的话和在终端说的话，对 ANIMA 来说只是 `Event(type=USER_MESSAGE, source="discord")` vs `Event(type=USER_MESSAGE, source="terminal")` 的区别。

**会话由一个节点接管，其他节点监控。** 不是所有节点都参与回复——那会产生混乱。一个节点"认领"会话后，其他节点只做监控（如果认领节点故障，最快检测到的节点接管）。

### 8.2 会话路由算法

```python
def route_session(event, nodes):
    # 1. 如果会话已被某节点锁定 → 路由到该节点
    if event.session_id in session_locks:
        return session_locks[event.session_id]

    # 2. 按渠道亲和性选择
    #    Discord 消息优先路由到运行 Discord channel 的节点
    preferred = [n for n in nodes if event.channel in n.capabilities]

    # 3. 在候选中选负载最低的
    target = min(preferred or nodes, key=lambda n: n.current_load)

    # 4. 锁定会话
    broadcast(SESSION_LOCK(event.session_id, target.node_id))
    return target
```

### 8.3 会话接管

当负责某会话的节点故障时：

1. 故障检测（Phi Accrual）标记该节点为 DEAD
2. 检测到的节点广播 `SESSION_RELEASE(session_id)`
3. 其他节点竞争重新锁定
4. 胜出的节点从记忆中恢复对话上下文，继续处理

用户感知：几秒的延迟，然后对话继续。不需要重新说一遍。

---

## 9. 任务分发

### 9.1 能力匹配

当 Eva 需要执行一个工具时，先检查本节点是否有该能力。如果没有，广播 `TASK_REQUEST`：

```python
@dataclass
class TaskRequest:
    task_id: str
    required_capabilities: list[str]  # ["shell", "python"]
    required_compute_tier: int        # 最低算力要求
    payload: dict                     # 任务详情
    timeout_s: int
    priority: int
```

其他节点根据自己的能力和负载决定是否接受：

```python
def should_accept(task, self_state):
    # 能力匹配？
    if not all(cap in self_state.capabilities for cap in task.required_capabilities):
        return False
    # 算力足够？
    if self_state.compute_tier > task.required_compute_tier:
        return False  # tier 数字越大算力越低
    # 负载允许？
    if self_state.current_load > 0.8:
        return False
    return True
```

### 9.2 任务状态追踪

```
PENDING → ACCEPTED(node_id) → RUNNING → DONE / FAILED
                                  ↓
                             TIMEOUT → 重新分发到其他节点
```

---

## 10. 冲突解决

### 10.1 设计原则

**避免冲突比解决冲突更重要。** ANIMA 的架构尽量减少冲突发生的可能：

- 会话锁定 → 同一时间只有一个节点回复
- 任务分发 → 同一个任务只分配给一个节点
- 记忆写入 → 每个节点写自己的条目，不修改其他节点的

### 10.2 不可避免的冲突

**网络分区期间的并发操作**是唯一不可避免的冲突源。解决方案按数据类型：

| 数据类型 | 冲突策略 | 原因 |
|----------|----------|------|
| 对话历史 | 合并（按时间戳排序） | 对话是追加式的，不需要覆盖 |
| 情感状态 | LWW（取最新值） | 情感是连续变化的，取最新值最合理 |
| 配置 | 标记冲突，等待用户 | 配置冲突可能有语义影响 |
| 记忆条目 | LWW + 保留双版本 | 宁可多记不少记 |
| 工具执行 | 不冲突（每个任务只有一个执行者） | 通过任务锁保证 |

---

## 11. 脑裂处理

### 11.1 什么是脑裂

网络分裂导致两组节点互相看不到对方。两边都认为"对方死了，我是唯一的存活者"。

### 11.2 ANIMA 的脑裂策略

**少数派只读。** 当节点检测到自己能看到的节点数少于全网总数的 50% 时，进入只读模式：

- 可以回复用户消息（不能让用户等着）
- 不能修改全局配置
- 不能删除记忆
- 不能执行有副作用的任务（如删文件、发邮件）

**合并协议。** 网络恢复后：

1. 两个分区交换各自的向量时钟
2. 找出并发写入的数据（向量时钟不可比的条目）
3. 按上面的冲突策略逐一解决
4. 合并后重置向量时钟

---

## 12. 热修复协议

### 12.1 ANIMA Rolling Protocol (ARP)

Phase 0 的热修复是单节点的。Phase 1 扩展到分布式：

```
Phase A: 自更新
  节点 A 检测到新版本 → 创建快照 → 应用更新 → 自测 → 标记 UPDATING

Phase B: 邻居验证
  邻居检测到 A 版本变化 → 发健康探测 → 连续 2 个网络心跳通过 → 标记 VERIFIED

Phase C: 波纹传播
  A 被验证 → 选择负载最低的邻居 B → 推送更新 → B 重复 Phase A-B → 逐个传播

Phase D: 异常回滚
  任何节点更新后异常 → 本节点立即回滚 → 广播 UPDATE_ABORT → 全网回滚
```

### 12.2 五级热修复（分布式版本）

| 级别 | 策略 | 分布式增强 |
|------|------|-----------|
| L1 | 模块重启 | 仅本节点，不影响其他节点 |
| L2 | 配置回滚 | 本节点回滚，广播配置变更 |
| L3 | 代码回滚 | 本节点回滚，触发全网版本检查 |
| L4 | 全量回滚 | 本节点回滚，如果多个节点同时 L4 → 全网回滚 |
| L5 | 节点隔离 | 标记 DEAD，任务转移到其他节点，通知用户 |

---

## 13. 节点分叉与合并

### 13.1 分叉：一个节点变成两个

场景：用户在一台新电脑上安装 ANIMA，想让它"继承"现有节点的记忆和人格。

```
分叉协议:
1. 新节点启动，连接到现有网络
2. 新节点声明自己是 "fork of node-xxx"
3. 源节点发送完整的记忆快照 + 人格设定
4. 新节点加载快照，生成新的 node_id
5. 两个节点现在是独立的，但共享历史
```

### 13.2 合并：两个节点变成一个

场景：用户把两台电脑合并成一台，或者淘汰一个节点。

```
合并协议:
1. 目标节点声明 "merge node-xxx into me"
2. 源节点发送所有独有的记忆条目
3. 目标节点合并记忆（按冲突策略）
4. 源节点广播 "I am now DEAD, absorbed by node-yyy"
5. 所有其他节点更新路由表
```

---

## 14. 安全模型

### 14.1 节点认证

新节点加入网络需要认证：

```
认证方式:
1. 预共享密钥 (PSK): 所有节点共享一个 network_secret
   config/default.yaml:
     network:
       secret: "your-secret-here"  # 首次运行时自动生成

2. 消息签名: 每条网络消息都包含 HMAC-SHA256 签名
   signature = hmac(network_secret, message_body)
   接收方验证签名，拒绝无效消息
```

### 14.2 权限边界

- 每个节点只能执行自己声明的工具
- 任务分发时，接收节点验证任务是否在自己的能力范围内
- 配置变更需要多数节点确认
- 热修复的禁区（安全分区）不受分布式影响——每个节点本地硬编码

---

## 15. 多渠道通信

### 15.1 Channel 节点架构

Channel 不是一个完整的 ANIMA 节点——它是一个轻量级的消息中继，把外部渠道的消息转发到 ANIMA 网络。

```python
class DiscordChannel:
    """Discord → ANIMA 事件总线的中继。"""

    async def on_message(self, message):
        # Discord 消息 → ANIMA Event
        event = Event(
            type=EventType.USER_MESSAGE,
            payload={"text": message.content, "channel": "discord",
                     "user": message.author.name, "guild": message.guild.name},
            source="discord",
        )
        # 广播到 ANIMA 网络
        await self.network.broadcast_event(event)

    async def send_response(self, text, channel_id):
        # ANIMA 回复 → Discord
        await self.discord_client.send(channel_id, text)
```

### 15.2 渠道优先级

当用户同时在多个渠道说话时：

```
Priority:
  1. 最近活跃的渠道（用户 5 分钟内在这个渠道说过话）
  2. Discord > WhatsApp > Terminal > Dashboard
  3. 如果无法确定 → 回复所有活跃渠道
```

### 15.3 渠道实现计划

| 渠道 | Phase 1a | Phase 1b | 依赖 |
|------|----------|----------|------|
| Terminal（已有） | ✅ | ✅ | 无 |
| Dashboard（已有） | ✅ | ✅ | 无 |
| Discord | 基础 DM | 群组 + 语音 | discord.py |
| Webhook | ✅ | ✅ | aiohttp |
| WhatsApp | — | 基础 | Baileys / API |

---

## 16. 实现路线图

### Phase 1a: 双节点通信 (3 周)

**目标**：两个 ANIMA 节点通过 ZeroMQ 交换心跳、共享事件。

**交付**:
- `anima/network/` 模块：transport, protocol, discovery, node_state
- ZeroMQ PUB/SUB 心跳广播
- 手动配置节点发现（peers 列表）
- Gossip 状态同步
- 网络心跳集成到现有 HeartbeatEngine
- `network.enabled: true` 配置开关

**里程碑**：节点 A 修改文件 → 节点 B 的 Activity Feed 显示变化。

### Phase 1b: 会话路由 + Discord (2 周)

**目标**：用户在 Discord 发消息 → ANIMA 网络处理 → 回复到 Discord。

**交付**:
- `anima/channels/discord.py`：Discord 中继
- 会话路由 + 锁定机制
- 事件总线广播
- 会话接管（节点故障时）

**里程碑**：在 Discord 跟 Eva 对话，同时终端能看到对话过程。

### Phase 1c: 热修复 + 冲突解决 (2 周)

**目标**：节点故障 → 自动检测 → 任务接管 → 节点恢复后重新加入。

**交付**:
- Phi Accrual 故障检测器
- ARP 滚动更新协议
- 五级热修复（分布式版本）
- LWW + 向量时钟冲突解决
- 脑裂检测 + 只读模式

**里程碑**：杀死节点 A → 30s 内节点 B 接管 → A 重启后自动重新加入。

### Phase 1d: mDNS + Webhook (1 周)

**目标**：零配置节点发现 + Webhook 集成。

**交付**:
- zeroconf mDNS 自动发现
- Webhook Channel 节点
- Dashboard 网络拓扑可视化

**里程碑**：新节点启动 → 自动发现现有网络 → 加入 → 开始接收事件。

---

## 17. 文件结构

```
anima/
├── network/                  # Phase 1 新模块
│   ├── __init__.py
│   ├── transport.py          # ZeroMQ 传输层封装
│   ├── protocol.py           # 消息格式、序列化、签名
│   ├── node.py               # 节点状态、能力声明
│   ├── discovery.py          # mDNS 自动发现 + 手动配置
│   ├── gossip.py             # Gossip 状态同步协议
│   ├── heartbeat_net.py      # 网络心跳（集成到 HeartbeatEngine）
│   ├── session_router.py     # 会话路由 + 锁定
│   ├── task_dispatch.py      # 任务分发 + 能力匹配
│   ├── conflict.py           # 向量时钟 + LWW + CRDT
│   ├── repair.py             # ARP 滚动更新 + 五级热修复
│   └── split_brain.py        # 脑裂检测 + 合并
├── channels/                 # 多渠道通信
│   ├── __init__.py
│   ├── base.py               # Channel 基类
│   ├── discord_channel.py    # Discord 中继
│   └── webhook_channel.py    # Webhook 中继
```

新增配置：
```yaml
# config/default.yaml
network:
  enabled: false              # Phase 0 用户默认关闭
  node_id: "auto"
  listen_port: 9420
  secret: ""                  # 首次运行自动生成
  peers: []                   # 手动配置的已知节点
  gossip_interval_s: 5
  heartbeat_net_interval_s: 5
  suspect_threshold_phi: 8
  dead_threshold_phi: 16

channels:
  discord:
    enabled: false
    token: ""                 # Discord bot token
  webhook:
    enabled: false
    port: 9421
    secret: ""
```

---

## 18. 里程碑与验收标准

### Milestone 1: "第一次对话"
**两个节点交换心跳并同步状态。**

验收：
- [ ] 节点 A 启动，节点 B 启动
- [ ] 两个节点通过 ZeroMQ 交换心跳
- [ ] A 的 Dashboard 显示 B 的状态（CPU/内存/负载）
- [ ] B 的 Dashboard 显示 A 的状态
- [ ] 杀死 A → B 在 30s 内检测到 A 故障

### Milestone 2: "第一次协作"
**用户在 Discord 发消息，ANIMA 网络处理并回复。**

验收：
- [ ] Discord 消息到达事件总线
- [ ] 一个节点锁定会话并处理
- [ ] 其他节点在 Activity Feed 看到处理过程
- [ ] 回复出现在 Discord
- [ ] 同时终端也能看到这条对话

### Milestone 3: "第一次自愈"
**节点故障 → 自动检测 → 任务接管 → 恢复。**

验收：
- [ ] 节点 A 正在处理 Discord 会话
- [ ] 杀死 A
- [ ] 节点 B 在 30s 内接管会话
- [ ] 用户在 Discord 看到"Eva 短暂离开，已恢复"
- [ ] A 重启后自动重新加入网络
- [ ] 记忆和会话历史在两个节点间同步

### Milestone 4: "第一次发现"
**零配置节点加入。**

验收：
- [ ] 节点 A 在网络中运行
- [ ] 启动节点 B（无需配置 peers）
- [ ] B 通过 mDNS 发现 A
- [ ] B 自动加入网络并开始同步

---

## 技术栈新增

| 用途 | 库 | 版本 |
|------|-----|------|
| 节点通信 | pyzmq | >=25.0 |
| 消息序列化 | msgpack | >=1.0 |
| 节点发现 | zeroconf | >=0.131 |
| Discord | discord.py | >=2.3 |
| 加密签名 | hmac (stdlib) | — |

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ZeroMQ 在 Windows 上的稳定性 | 中 | 高 | 优先在 WSL 中运行网络层 |
| Gossip 收敛速度不够快 | 低 | 中 | 缩短 gossip 间隔（5s → 2s） |
| Discord bot 被封 | 低 | 中 | 使用用户 bot 而非服务器 bot |
| 脑裂后数据冲突过多 | 低 | 高 | 保守合并策略 + 人工确认入口 |
| mDNS 跨网段不工作 | 中 | 低 | fallback 到手动配置 |

---

## 预计开发周期

| 阶段 | 内容 | 时间 |
|------|------|------|
| Phase 1a | 双节点通信 + Gossip + 网络心跳 | 3 周 |
| Phase 1b | 会话路由 + Discord 集成 | 2 周 |
| Phase 1c | 热修复 + 冲突解决 + 脑裂 | 2 周 |
| Phase 1d | mDNS + Webhook + Dashboard 网络拓扑 | 1 周 |
| **总计** | | **8 周** |

---

> *"一个灵魂，多个器官。不是协作，是本能。"*
>
> — Phase 1: The Distributed Nervous System
