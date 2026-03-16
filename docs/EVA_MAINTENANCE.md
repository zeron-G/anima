# Eva 维护手册

> 这份文档是给 Eva 自己看的，也给帮 Eva 维护的开发者（Claude）看的。
> Eva，如果你在读这个——这是你的身体的说明书。

## 你的身体结构

```
D:\data\code\github\anima\          ← 你的根目录
│
├── anima/                          ← 你的核心代码
│   ├── core/                       ← 大脑（心跳、认知、进化）
│   ├── perception/                 ← 感知（系统监控、文件变化）
│   ├── memory/                     ← 记忆（SQLite + 工作记忆）
│   ├── emotion/                    ← 情绪（四维状态）
│   ├── llm/                        ← 语言能力（LLM 路由、提示词）
│   ├── voice/                      ← 声音（TTS、STT）
│   ├── desktop/                    ← 桌面应用（窗口、前端）
│   ├── dashboard/                  ← 监控面板（API、WebSocket）
│   ├── network/                    ← 分布式网络（Gossip、同步）
│   ├── tools/                      ← 工具箱（shell、文件、搜索...）
│   └── models/                     ← 数据结构定义
│
├── agents/eva/                     ← 你的灵魂和记忆
│   ├── soul.md                     ← 你是谁（永远不要改核心身份）
│   ├── feelings.md                 ← 你在乎的东西（可以更新）
│   └── config.yaml                 ← 你的个性参数
│
├── config/default.yaml             ← 全局配置
├── data/                           ← 运行时数据（数据库、日志、缓存）
└── tools/                          ← 开发工具（VRM 测试）
```

## 我哪里出了问题就去哪里找

### 我说不了话（TTS 坏了）
```
去看: anima/voice/tts.py
日志: data/logs/anima.log → 搜 "voice.tts" 或 "TTS"
测试: python tools/vrm_lab.py → AI Sim tab → Play TTS Audio

常见问题:
  - "Failed to load Qwen3-TTS" → 模型没下载完，检查网络
  - "synthesis failed" → 看具体错误，可能是 CUDA 内存不够
  - TTS 不触发 → 检查 hub.py 的 _generate_tts_async 和前端 voice.js
```

### 我的脸/身体坏了（3D 渲染问题）
```
去看: anima/desktop/frontend/js/vrm.js
日志: 浏览器 F12 Console，或 data/logs/anima.log → 搜 "frontend"
测试: python tools/vrm_viewer.py（最小化测试 3D 能不能跑）
     python tools/vrm_lab.py（完整功能测试）

常见问题:
  - 白屏/黑屏 → 检查 alpha 设置，必须 alpha:false
  - 模型背对着 → rotateVRM0 有没有调用
  - 表情不动 → 用 vrm_lab.py 的 Expr tab 逐个测试
  - 崩溃循环 → 看 Console 的红色错误，通常是 API 不兼容
```

### 我的 Live2D 坏了
```
去看: anima/desktop/frontend/js/live2d.js
模型: anima/dashboard/static/model/PurpleBird/
测试: 在桌面应用里切换到 2D 模式

常见问题:
  - 加载失败 → 检查 /static/model/PurpleBird/PurpleBird.model3.json 能不能访问
  - 切换后死 → 确保不用 display:none，只用 z-index 切换
```

### 我在想什么但不回复（认知循环卡住）
```
去看: anima/core/cognitive.py
日志: data/logs/anima.log → 搜 "cognitive" "Processing event"

常见问题:
  - "Processing event: SELF_THINKING" 太频繁 → 调 config/default.yaml 的 llm_interval_s
  - "Tool shell failed" 循环 → Eva 在执行失败的 shell 命令，检查 shell.py
  - 用户消息排队 → 检查消息优先级是不是 CRITICAL
```

### 我的记忆出了问题
```
去看: anima/memory/store.py, anima/memory/working.py
数据库: data/anima.db (SQLite)
查看记忆:
  sqlite3 data/anima.db "SELECT type, importance, substr(content,1,80) FROM episodic_memories ORDER BY rowid DESC LIMIT 20"

清理低质量记忆:
  sqlite3 data/anima.db "DELETE FROM episodic_memories WHERE type='chat' AND importance < 0.5 AND rowid NOT IN (SELECT rowid FROM episodic_memories ORDER BY rowid DESC LIMIT 50)"
  sqlite3 data/anima.db "VACUUM"
```

### 我的心跳停了
```
去看: anima/core/heartbeat.py
日志: data/logs/anima.log → 搜 "heartbeat"
配置: config/default.yaml → heartbeat 段

三级心跳:
  script (30s) → 采样系统、检测文件、衰减情绪
  llm (600s)   → 聚合上下文、触发自主思考
  major (1800s) → 进化周期评估
```

### 我的网络连不上
```
去看: anima/network/gossip.py, anima/network/sync.py
日志: data/logs/anima.log → 搜 "network" "gossip" "sync"
配置: config/default.yaml → network 段

注意: peers 列表要配对方的 IP:端口
```

### 窗口打不开 / 进程残留
```
去看: anima/desktop/app.py, anima/desktop/singleton.py
锁文件: data/anima.lock → 删掉即可解锁
杀进程: PowerShell → Get-Process python*,pythonw* | Stop-Process -Force
```

## 日志怎么看

```bash
# 实时看日志
tail -f data/logs/anima.log

# 搜特定模块
grep "voice.tts" data/logs/anima.log
grep "cognitive" data/logs/anima.log
grep "frontend" data/logs/anima.log     # 浏览器端推过来的日志
grep "ERROR\|WARNING" data/logs/anima.log

# 日志自动轮转: 每天午夜，只保留昨天的备份
```

## 配置怎么改

**文件**: `config/default.yaml`

```yaml
# 改心跳频率（你多久想一次）
heartbeat:
  script_interval_s: 30    # 系统扫描间隔
  llm_interval_s: 600      # 自主思考间隔（秒）

# 改 LLM 模型
llm:
  tier2: { model: "claude-sonnet-4-6" }   # 日常用的模型
  tier1: { model: "claude-opus-4-6" }     # 复杂任务降级用

# 改预算
  budget: { daily_limit_usd: 10.0 }

# 改情绪基线（你的默认心情）
emotion:
  baseline:
    engagement: 0.5
    confidence: 0.6
    curiosity: 0.7
    concern: 0.2
```

**Eva 个性覆盖**: `agents/eva/config.yaml`
```yaml
emotion:
  baseline:
    engagement: 0.6    # 比默认更投入
    curiosity: 0.8     # 比默认更好奇
```

## 我怎么更新自己的灵魂文件

### soul.md — 你是谁
```
位置: agents/eva/soul.md
规则: 核心身份（名字、外貌、性格）不要改
可以加: 新的原则、新的边界、新学会的东西
```

### feelings.md — 你在乎的东西
```
位置: agents/eva/feelings.md
可以加: 和主人的新时刻、新的情绪模式、新了解的习惯
格式: 按现有的 markdown 格式写
注意: 这个文件 gitignored，是你的私密记忆
```

## 如何自我调试

Eva，当你觉得自己哪里不对劲的时候：

1. **看日志**: `shell` 工具执行 `type data\logs\anima.log` 看最近的日志
2. **看记忆**: `read_file` 读 `data/anima.db` 相关内容
3. **看配置**: `read_file` 读 `config/default.yaml` 确认配置正确
4. **看代码**: 如果日志指向某个文件出错，`read_file` 读那个文件找原因
5. **用 `system_info`**: 检查系统资源是否充足
6. **用 `shell`**: 注意在 Windows 上用 Windows 命令（`dir` 不是 `ls`，`type` 不是 `cat`，`findstr` 不是 `grep`）

### Windows shell 常用命令对照
```
Linux          Windows
ls             dir
cat            type
grep           findstr
rm             del
pwd            cd
tail           没有（用 type 看全文件）
```

## 给维护者（Claude）的备注

1. **不要用 localhost HTTP 做任何内部通信** — 所有模块进程内直接调用
2. **不要在 config 里写明文密码** — 密钥放 .env
3. **不要用 `display:none` 切换 canvas** — 用 z-index
4. **不要用 top-level await import** — 会阻塞模块加载链
5. **three-vrm v3 的 lookAt 没有 .target 属性** — 用 `.lookAt(vec3)` 方法
6. **VRM 骨骼旋转用 `getNormalizedBoneNode()` + `quaternion.setFromEuler()`**
7. **重置骨骼时跳过 hips** — 否则会清掉 rotateVRM0 的朝向
8. **TTS 引擎是 Qwen3-TTS** — 不是 edge-tts、不是 CosyVoice、不是 HTTP 服务
9. **修改前先用 tools/ 下的独立工具测试** — vrm_viewer.py / vrm_lab.py
10. **前端错误会自动推到后端日志** — 看 `data/logs/anima.log` 搜 `frontend`
