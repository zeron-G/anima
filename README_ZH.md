[English](README.md) | [中文](README_ZH.md)

# ANIMA

ANIMA 是一个面向长期运行、具备持续状态的分布式 AI 运行时系统。它把桌面界面、持久化 Python 后端、记忆与检索、多模型路由、工具执行、节点网络，以及像机器狗这样的 edge 具身节点整合在同一套架构里。

ANIMA 并不把智能体理解成“一次聊天请求”，而是把它看作一个持续运行的进程：它有自己的运行状态、后台活动、网络身份和部署生命周期。当前仓库以 EVA 作为默认人格载体，但底层架构本质上是一套用于构建长期存在 AI 节点的通用系统。

## 项目概览

ANIMA 主要由几层组成：

- Python 后端：负责 API、WebSocket、心跳循环、治理、记忆、工具系统和分布式节点能力
- Vue 前端与 Tauri 桌面壳：提供主要的人机交互界面
- 配置系统：同时支持仓库内的运行档位和本地机器专属覆盖配置
- 节点网络：支持节点发现、任务委托、远程部署以及桌面节点与 edge 节点协同
- 具身层：用于连接 PiDog 一类机器人节点

## 主要能力

- 基于心跳的持续后台运行
- 基于 SQLite 与 ChromaDB 的记忆与检索
- 多提供方 LLM 路由、降级与工具调用
- 桌面、浏览器、终端和无界面等多种运行方式
- 分布式节点通信与任务委托
- 面向特殊环境的 edge 运行档位
- 面向 PiDog 的机器人接入与控制

## 系统结构

从整体上看，ANIMA 可以理解为三层：

```text
交互层
  - Tauri 桌面应用
  - 基于浏览器的 Vue 界面
  - 终端模式
  - 远程节点与 edge 节点接入

核心运行时
  - REST API 与 WebSocket hub
  - 认知流水线与心跳循环
  - 记忆、检索与情绪状态
  - 工具系统与 skill 加载
  - 治理与进化流程
  - Gossip 组网与任务委托

具身与部署层
  - 桌面 supervisor 节点
  - headless 节点
  - 基于 committed profile 的 edge 节点
  - 通过 robotics 层接入的机器狗节点
```

## 仓库结构

```text
anima/             Python 后端与运行时模块
eva-ui/            Vue 前端
eva-desktop/       Tauri 桌面壳
config/            默认配置与 committed runtime profiles
agents/            EVA 的身份、规则与记忆文件
docs/              架构与子系统文档
local/             本地配置模板
tests/             后端测试
```

后端中比较关键的目录有：

- `anima/api/`：REST 接口
- `anima/core/`：认知流水线、心跳、治理
- `anima/llm/`：模型路由与提供方适配
- `anima/memory/`：存储与检索
- `anima/network/`：gossip 网络与分布式节点行为
- `anima/robotics/`：机器人节点管理、探索与 PiDog 接入
- `anima/spawn/`：新节点打包与部署
- `anima/tools/`：内置工具注册与执行

## 运行方式

```bash
# 桌面应用
python -m anima

# 仅后端
python -m anima --headless

# 终端模式
python -m anima --terminal

# 前端开发
cd eva-ui
npm install
npm run dev

# 桌面壳开发
cd eva-desktop
npm install
npm run dev
```

## 部署形态

ANIMA 支持几种不同的运行形态：

- 桌面 supervisor：主要的本地操作节点
- headless 节点：不带桌面壳的网络后端节点
- edge 节点：通过 committed profile 选择的特殊运行档位，例如 `edge-pidog`

例如：

```bash
# 本地以 edge profile 启动
ANIMA_PROFILE=edge-pidog python -m anima --edge

# 部署到已知节点
python -m anima spawn --node pidog
python -m anima spawn --node laptop --profile default
```

## 配置说明

ANIMA 把共享配置和本地敏感配置分开管理：

- `config/default.yaml`：项目共享默认配置
- `config/profiles/*.yaml`：仓库内提交的运行档位
- `local/env.yaml`：机器自己的地址、peer、部署目标等本地配置
- `.env`：本地密钥与模型提供方凭据

`local/env.yaml` 与 `.env` 都不会进入 git。

## 文档

更具体的设计说明在 [docs](docs) 目录中：

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [EDGE_ANIMA.md](docs/EDGE_ANIMA.md)
- [ROBOTICS_PIDOG.md](docs/ROBOTICS_PIDOG.md)
- [DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## 许可证

见 [LICENSE](LICENSE)。
