# ANIMA Desktop 重构计划

> 目标：将 Eva 的桌面端从"套壳 WebView"升级为工业级 AI 伴侣应用

---

## 设计语言

**视觉基调**：冰蓝 × 暗夜 — 赛博芭蕾天使的优雅栖息地

- **主色**：`#60c8d0` (冰蓝/cyan) — Eva 的瞳色
- **辅色**：`#a78bfa` (薰衣草紫), `#f778ba` (玫瑰粉)
- **背景**：`#06060a` → `#0a0a10` → `#14141e` (三层深度)
- **玻璃态**：`rgba(255,255,255,0.04)` 卡片 + `rgba(255,255,255,0.06)` 边框
- **字体**：Instrument Serif (标题) + Inter/DM Sans (正文) + Cascadia Code (代码)
- **动效**：流畅过渡，呼吸脉搏，柔和渐入渐出

---

## 功能清单

### P0: 核心功能 (必须)

1. **顶部状态栏**
   - 当前活跃模型显示 (Opus / Sonnet / Local Qwen)
   - 降级/恢复指示器 (绿=正常, 黄=降级, 红=全挂→本地)
   - idle_score 仪表 + 级别 (BUSY/LIGHT/MODERATE/DEEP)
   - API 花费 (今日 $X.XX / $10.00)
   - 网络节点数 + 连接状态

2. **Toast 通知系统**
   - 模型降级通知 (⚠️ Opus → Sonnet)
   - 模型恢复通知 (✅ 恢复到 Opus)
   - 进化成功/失败通知
   - 系统告警 (CPU/磁盘)
   - 右上角弹出，5s 自动消失，可手动关闭

3. **设置面板 (Settings Page 重构)**
   - **LLM 配置**：Tier1/Tier2 模型下拉选择器，实时热切换
   - **本地模型**：服务器路径、模型路径、GPU 层数、上下文大小
   - **心跳**：脚本/LLM/进化间隔调节滑块
   - **空闲调度**：启用/禁用、阈值调节、预算设置
   - **API Key 管理**：OAuth token 状态、API key 输入框（密码遮掩）
   - **通道**：Discord 启用/禁用、Webhook 配置
   - **所有设置即时保存，无需重启**

### P1: 增强体验

4. **重构布局**
   - 顶部固定状态栏 (40px)
   - 左侧导航栏优化 (图标+文字，收展)
   - 主内容区自适应
   - 底部对话输入栏固定

5. **Overview 页面增强**
   - 系统仪表盘 (CPU/MEM/DISK 圆环图)
   - 情感状态雷达图
   - 空闲调度器实时状态
   - 当前模型 + 降级链路可视化

6. **进化管理器 (Evolution Page 增强)**
   - 进化历史时间线
   - 目标进度条
   - 手动触发/暂停进化
   - 失败原因查看

7. **实时日志查看器**
   - 日志流 (WebSocket 实时推送)
   - 按级别过滤 (INFO/WARNING/ERROR)
   - 关键词搜索
   - 自动滚动 + 暂停

### P2: 高级功能

8. **环境浏览器**
   - env_catalog 搜索界面
   - 分类过滤 (代码/文档/配置/媒体)
   - 扫描进度显示

9. **对话管理**
   - 历史会话列表
   - 搜索聊天记录
   - 导出对话

10. **系统托盘** (后续版本)
    - 最小化到托盘
    - 快捷菜单
    - 开机自启

---

## 实现方案

### 架构

所有 UI 在 `anima/desktop/frontend/` 中，通过 PyWebView 加载。
后端 API 在 `anima/dashboard/server.py`，通过 WebSocket + REST 通信。

需要新增的后端 API：
- `GET /api/llm/status` — 当前模型、降级状态、级联链路
- `POST /api/llm/switch` — 热切换模型
- `GET /api/logs/stream` — SSE 实时日志流
- `GET /api/env/search` — 环境搜索
- `GET /api/env/stats` — 环境统计

### 文件结构

```
anima/desktop/frontend/
├── index.html           # 主入口 (重构)
├── css/
│   └── style.css        # 完整样式 (重构)
├── js/
│   ├── app.js           # 主控制器 (重构)
│   ├── vrm.js           # VRM 引擎 (保持)
│   ├── live2d.js        # Live2D 引擎 (保持)
│   ├── voice.js         # 语音管理 (保持)
│   ├── notifications.js # 新: Toast 通知系统
│   └── settings.js      # 新: 设置面板逻辑
└── model/               # 3D/2D 模型资产 (保持)
```
