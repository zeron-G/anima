# ANIMA 开发文档

## 环境配置

### 必需
- Python 3.11+
- Windows 11 (WebView2)
- NVIDIA GPU + CUDA (用于 Qwen3-TTS)

### Python 环境
```bash
# Conda
conda create -n anima python=3.11
conda activate anima
pip install -e ".[tts,dev]"

# Qwen3-TTS 依赖
pip install torch torchaudio soundfile librosa transformers==4.57.3 accelerate einops
# qwen_tts 包需从 WSL 环境复制或从源码安装
```

### 启动模式
| 命令 | 说明 |
|------|------|
| `python -m anima` | 桌面应用 (默认) |
| `python -m anima --headless` | 无窗口，浏览器访问 |
| `python -m anima --legacy` | 终端模式 |
| `python -m anima --experimental` | 允许多实例 |
| `python -m anima watchdog` | 看门狗模式 |
| `python -m anima --watch` | 热重载开发模式 |

## 代码架构

### 启动流程
```
__main__.py → desktop/app.py
  → launch_desktop()
    → singleton.acquire_lock()        # 确保单实例
    → threading.Thread(_run_backend)  # 后台线程跑后端
    → webview.start()                 # 主线程跑窗口
    → os._exit(0)                     # 关窗口杀全部
```

### 后端启动 (main.py → run())
```
load_config()
  → 初始化所有子系统:
    EventQueue, SnapshotCache, DiffEngine,
    WorkingMemory, MemoryStore, EmotionState,
    ToolRegistry, ToolExecutor, RuleEngine,
    LLMRouter, PromptBuilder, HeartbeatEngine,
    AgenticLoop, TerminalUI, DashboardHub, DashboardServer
  → 如果 network.enabled:
    GossipMesh, MemorySync, SplitBrainDetector,
    SessionRouter, Channels (Discord/Webhook)
  → 启动心跳 + 认知循环 + 终端
  → 等待 shutdown 信号
```

### 前端架构
```
index.html
  → boot sequence (CSS 动画)
  → app.js (ES module, main controller)
    → vrm.js (VRM 3D 渲染，懒加载 three-vrm)
    → live2d.js (PIXI Live2D，PIXI 全局脚本)
    → voice.js (TTS 播放 + viseme 引擎 + STT 录音)
  → WebSocket 连接 /ws (2s 推送全量快照)
```

### 两个 canvas 的管理
- 两个 canvas 都用 `position:absolute; inset:0` 永远在 DOM 中
- 通过 CSS class `av-front` (z-index:2) 控制谁在前面
- 切换模式只改 z-index + pause/resume 渲染循环
- **绝不用 `display:none`**（PIXI 无法在不可见 canvas 上初始化 WebGL）
- **绝不 destroy/recreate**（WebGL context 不能在同一 canvas 上重建）

## VRM 集成注意事项

### three-vrm v3 API 差异
| v2 | v3 | 备注 |
|----|----|----|
| `vrm.lookAt.target.set(x,y,z)` | `vrm.lookAt.lookAt(vec3)` | v3 无 .target 属性 |
| `VRMExpressionPresetName.Aa` | `setValue('aa', val)` | 字符串名也行 |
| 直接 `bone.rotation.set()` | `getNormalizedBoneNode() + quaternion.setFromEuler()` | 必须用 humanoid API |

### Flare 模型特性
- **VRM 0.x** 格式 (需要 `rotateVRM0`)
- **aa 表情存在但可能无视觉效果** — 用 ARKit blend shapes (JawOpen, MouthFunnel 等) 作为补充
- **换装**: 只有 3 个 mesh 名含 `Costume` 是衣服
- **骨骼重置**: 跳过 `hips` 骨骼 (否则会清掉 rotateVRM0 的旋转)

### Viseme 口型引擎
```
音频 WAV/MP3
  → fetch + decodeAudioData → Float32Array PCM
  → 切 25ms 帧 (40fps)
  → 每帧计算:
    - RMS (总能量)
    - Zero-Crossing Rate (频率近似)
    - High-freq Energy Ratio (前后元音区分)
  → ZCR + hfRatio → 元音分类:
    - ZCR < 1500 → /a/, /o/ (开口)
    - ZCR 1500-3500 → /e/ (中间)
    - ZCR > 3500 → /i/, /u/ (闭口)
  → 映射到 blend shapes: aa, oh, ee, ih, ou, JawOpen, MouthFunnel, MouthSmile
  → 2-pass 3-frame 滑动平均平滑
  → 生成 timeline: [{t, aa, oh, ...}, ...]
  → 播放时 binary search + 线性插值 @ 60fps
```

## TTS 集成

### Qwen3-TTS
- 模型: `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` (首次自动下载)
- API: `model.generate_custom_voice(text, language, speaker, instruct)`
- 返回: `(wavs, sample_rate)` → `sf.write(path, wavs[0], sr)`
- 后端自动生成: `hub.add_chat_message("agent", text)` → `create_task(synthesize())` → `tts_url` 写入消息
- 前端: WebSocket 推送带 `tts_url` 的消息 → `voiceManager.playUrl(url)` → viseme 分析 → 播放

## Bug 修复记录

| 问题 | 根因 | 修复 |
|------|------|------|
| Boot 卡在 "Initializing..." | vrm.js 顶层 `await import()` 阻塞模块链 | 移到 `init()` 内 lazy import |
| VRM 白屏/空 | `alpha:true` → 场景透明看不见 | `alpha:false` + `setClearColor` |
| VRM lookAt 崩溃 | three-vrm v3 无 `.target` 属性 | 用 `.lookAt(vec3)` 方法 |
| VRM T-pose (大字) | `quaternion.identity()` 重置了 hips | 跳过 hips 骨骼 |
| VRM pose 不生效 | 直接 `rotation.set()` 无效 | `getNormalizedBoneNode()` + quaternion |
| Live2D 切换后死 | `display:none` 时 PIXI 不能初始化 | 两个 canvas 永远可见，z-index 切换 |
| TTS 第二次不触发 | `createMediaElementSource` 只能调一次 | 每次新建 Audio 元素 |
| TTS 用错引擎 | 代码引用 CosyVoice/localhost:9001 | 改用 Qwen3-TTS 本地 CUDA |
| Python 找不到 (Eva shell) | pythonw.exe 不在 PATH | shell 工具替换 `python` 为完整路径 |
| Eva 回复慢 | SELF_THINKING 每 3 分钟抢占认知循环 | 改为 10 分钟，提高触发阈值 |
| Debug overlay 遮挡 | info 日志也触发显示 | 只在 error/warning 时显示 |
| 进程残留 | 关窗口后后端线程继续跑 | `os._exit(0)` 在 webview.start() 后 |

## 日志系统

- 文件: `data/logs/anima.log`
- 格式: `TimedRotatingFileHandler(when="midnight", backupCount=1)` — 24 小时自动轮转
- 前端错误自动 POST 到 `/api/debug` → 写入同一日志文件
- 查看: `tail -f data/logs/anima.log`
