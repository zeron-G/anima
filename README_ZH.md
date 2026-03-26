[English](README.md) | [中文](README_ZH.md)

# ANIMA

**心跳驱动的分布式自进化 AI 生命体系统**

ANIMA 不是聊天机器人——它是一个拥有心跳、情绪、记忆、感知能力，并且能自主进化自身代码的 AI 生命体。

## 快速启动

```bash
# Windows: 双击
ANIMA.bat

# 或命令行
python -m anima              # 桌面应用（PyWebView 窗口）
python -m anima --headless   # 无窗口（浏览器访问 localhost:8420/desktop）
python -m anima --legacy     # 终端模式
```

## 架构

```
ANIMA 桌面应用
│
├── PyWebView 原生窗口 (WebView2)
│   ├── VRM 3D 模式 (Three.js + three-vrm, Flare 模型)
│   ├── Live2D 2D 模式 (PIXI, PurpleBird 模型)
│   ├── 聊天面板 + 实时活动流
│   └── Qwen3-TTS 语音合成 + Viseme 口型同步
│
├── Python 后端 (aiohttp)
│   ├── 三级心跳引擎 (脚本30s / LLM 10min / 主要30min)
│   ├── 认知循环 (规则引擎 → LLM 智能体循环)
│   ├── 记忆系统 (SQLite + 工作记忆)
│   ├── 情绪状态 (参与/自信/好奇/担忧 四维)
│   ├── 工具执行器 (13+ 内置工具)
│   ├── Qwen3-TTS (本地 PyTorch CUDA 推理)
│   └── 进化引擎 (提案→执行→测试→部署→记录)
│
├── 分布式网络
│   ├── ZMQ Gossip 组网 + 记忆同步
│   └── 会话路由 + 脑裂检测
│
└── 通信渠道 (Discord / Webhook)
```

## 核心系统

### 心跳引擎
三个独立定时循环：
- **脚本心跳** (30s): 采样系统状态、检测文件变化、情绪衰减
- **LLM 心跳** (10min): 聚合上下文，触发自主思考
- **主要心跳** (30min): 评估进化候选

### 认知架构
```
事件 → 规则引擎(零成本) → 匹配? → 直接执行
                        → 不匹配 → LLM(Tier2 Sonnet → Tier1 Opus 降级)
                                    → 多轮工具调用 → 输出
```

### VRM 3D 形象
- **模型**: Flare (72 个表情, 53 个骨骼)
- **口型**: 音频 → PCM → 零交叉率+RMS → 元音分类 → viseme 时间线 → 60fps 插值
- **待机**: 呼吸 + 随机眨眼(2-7s) + 微摆
- **换装**: 3 个服装 mesh 可切换

### TTS 语音合成
- **Qwen3-TTS** (本地 CUDA 推理，无外部服务)
- 模型: `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`
- Agent 回复时自动生成，MD5 内容哈希缓存

### 机器狗具身节点
- 新增 PiDog Linux 平台接入层，支持把机器狗作为 ANIMA 的远程具身节点
- 后端提供 `/v1/robotics/*` REST 接口和内置工具，ANIMA 可直接下发动作、NLP 指令和语音
- EVA 桌面端新增 `/robotics` 页面，可直接控制机器狗并查看实时感知状态
- 当前探索能力为第一阶段：基于超声、触摸、姿态、电量的保守式自主探索
- 设计文档见 [docs/ROBOTICS_PIDOG.md](docs/ROBOTICS_PIDOG.md)

## 配置

编辑 `config/default.yaml`。密钥放 `.env` 文件。

## 开发

```bash
pip install -e ".[dev]"
pytest

# VRM 开发工具
python tools/vrm_viewer.py   # 模型查看器
python tools/vrm_lab.py      # 表情/骨骼/口型实验室
```

## 许可证

见 [LICENSE](LICENSE)。
