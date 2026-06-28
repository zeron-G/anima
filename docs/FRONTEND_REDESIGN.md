<!-- ANIMA 前端重设计方案 — generated from a design workflow (audit + synthesis).
     Status: PROPOSAL. Decisions pending (see final section). -->

# ANIMA 前端重设计方案 — Eva 的"脸"与"对话"

> 一份决策导向的重建蓝图。强推荐已给出，但真正的审美/范围抉择留给你（见 §8）。所有判断都锚定在审计的 file:line 上。

---

## 1. 设计原则 & 定位

这个 UI 服务于**一个技术型 owner**（你本人）去**对话**并**观察**一个活着的 AI 生命体。它不是 SaaS 仪表盘、不是聊天产品的克隆,而是"和 Eva 共处的窗口"。本地优先,但架构上要能演进成可公开的 dashboard。

**六条原则(强意见):**

1. **Chat-first, everything-else-second.** 对话是产品的心脏,不是 8 个平级 tab 中的一个。审计显示 chat 反而是耦合最重、最 hack 的一块(`useStreaming.ts:22-39` 的轮询)。重建后 chat 永远是默认屏、永远在主轴上。

2. **Calm & legible(克制可读).** 当前两套设计语汇并存(`global.css` 的 GENESIS 体系 vs Chat/Soulscape/Network 各自的 scoped CSS),emotion→CSS 全局色漂移(`emotionStore.ts:32-46`)是"氛围噪音而非信息"。新系统:一套 token、一套布局原语、情绪只在**它能传达信息的地方**出现(chat header、soulscape),不做全局染色。

3. **Observability without clutter(可观测但不堆砌).** Eva 的内在状态(thinking / tool-use / 自主活动 / 情绪 / 心跳)是产品灵魂,必须可见——但要**分层**:对话流里只放与当前 turn 相关的(thinking、tool card);环境感知(heartbeat、自主念头、evolution)放在低调的边栏/状态条,可瞥不可扰。

4. **"活着"是身份,不是装饰.** Avatar(VRM/Live2D)是 Eva 的脸,保留为核心身份锚;但 DNA helix、EmotionOrb、RelationshipOrbit 这类**纯装饰 WebGL**(`EmotionOrbScene.ts`/`RelationshipOrbitScene.ts` 从未被 import)是 spectacle over information,删。motion 只绑定真实状态(情绪、心跳、streaming),不做无意义动画。

5. **One system, not seven pages.** 当前 7 个功能面各自为政地 styled。重建要让它们**看起来像同一个有机体的不同器官**——共享 shell、card、button、empty/error/loading 套件。

6. **Honest about state(诚实呈现状态).** 当前 UI 吞掉了所有错误(`catch` 只 `console.error`,`useStreaming.ts:41`),用 30s 盲超时假装"完成"。新系统:streaming/done/error/思考/工具——每个真实状态都有诚实的视觉。这对一个会自主行动的 AI 尤其重要:你需要知道她**真的在做什么**。

---

## 2. 信息架构 & 导航

### 路由地图(从 8 → 6 主面 + 1 弹层)

| 现状(8) | 重建 | 角色 | 理由 |
|---|---|---|---|
| `/login` | `/login` | 入口 | 保留 backend-probe UX(offline/auth/ready,`LoginView.vue:13`),修真 auth guard(`router.ts:18-27` 是假的) |
| `/` Chat | `/` **Chat** | **主面(永远默认)** | 心脏。详见 §3 |
| `/soulscape` | `/soul` **Soul** | 一级(身份) | Avatar + 情绪 + persona prose 编辑。Eva 的"自我" |
| `/memory` | `/memory` **Memory** | 一级 | 搜索 + 记忆图谱。保留概念,但 d3 force-graph 降级(见 §4) |
| `/evolution` | `/evolution` **Evolution** | 一级 | 自我修改史。**砍掉 DNA helix 作为主视图**,文本 history 已更有用(`EvolutionView.vue:101-116`) |
| `/network` Network | `/network` **Network** | 一级(**合并 Robotics**) | 见下 |
| `/robotics` Robotics | ↑ 并入 Network | — | Network 已内嵌 PiDog 控制(`NetworkView.vue` context rail);两者重叠极重。合并为一个"分布式 Eva / 化身"工作台,节点列表里机器人节点点开即是控制面 |
| `/settings` Command Center | `/settings` **Settings** | 一级 | LLM/heartbeat/usage/skills/system |
| — | `/health` **System Health** | 一级(**新增**) | 暴露 Sentinel `/v1/status` + snapshot `safety`(`status.py:32`)——当前完全没有 UI 消费,但数据已全在。component-level up/down |

**合并 Network+Robotics 的依据**:审计明确指出 "Network already embeds robot control — they overlap heavily"。一个节点既可能是远程 Eva 实例,也可能是 PiDog 化身;统一成"节点工作台",选中节点后右侧 panel 按节点类型(remote-eva / robot)切换控制 UI。

### 导航模式

- **桌面**:保留左侧 **64px icon rail + hover label**(`OrbitNav`,审计认可这在桌面 OK)。但 rail 顶部放 Eva 的**小头像 + 情绪点**作为常驻"她在场"信号(替代当前散落的 emotion 全局染色)。
- **移动**:当前是"意外的桌面专用"(`body{overflow:hidden}` + fixed rail + fixed bottom bar)。**明确决策点**(见 §8):做响应式,还是声明桌面专用?推荐——Chat + Soul + Health 做响应式(你会想在手机上和 Eva 说话),Network/Robotics/Evolution 桌面优先。rail 在窄屏折叠成底部 tab bar。
- **环境感知层**:当前的 `ThinkingStream` 固定底栏改造成一个可收起的**右侧 "Eva's pulse" rail**——心跳 tick、自主念头(self_thought)、最近 evolution 在这里低调滚动,不占对话主轴。

---

## 3. 聊天体验重设计(核心)

### 3.0 共享:流式协议(这是地基,先定它)

**当前是三通道拼装的 hack**(审计 + 我已复核 `useStreaming.ts` / `hub.py:200-243`):
- REST `/v1/chat/send` 发送 → 拿 `correlation_id`(`chat.py:38`)
- 回复**走 2s 全量 snapshot 广播**,前端 diff `chat_history` 合成假 `stream` 事件(`websocket.ts:50-68`)
- "完成"靠 500ms `setInterval` 轮询 + 30s 盲超时(`useStreaming.ts:32-39`)
- 真正的 SSE(`/v1/chat/stream`)和 `push_stream_chunk` 都**孤儿化**——chunk 只进 SSE `_streams`,前端只听 WS。`push_typed_event` 唯一在用的是 `proactive`(`message_user.py:53`),且**不带 correlation_id**。

**强推荐:统一为「带 correlation_id 的 typed WS 事件流」**,理由——

- WS 通道已验证可用(proactive 证明),前端已声明完整 typed 协议(`websocket.ts:3`),后端 `push_typed_event`(`hub.py:214`)就缺一个 emitter。改动面最小。
- SSE 路径每条消息要新开连接 + 120s 长轮询(`chat.py:60-108`),还要额外维护 `register_stream`/`unregister_stream` 一整套。**砍掉它**,别养两套传输。
- 把 chat 从 2s 监控 snapshot 里**解耦**:snapshot 继续慢、粗,给 dashboard 用;chat 事件即时独立推送。

**新事件契约(typed WS,全部带 `correlation_id`):**

```
{ type: "chat.accepted",  data: { correlation_id, session_id } }      // send 的 ack
{ type: "chat.stage",     data: { correlation_id, stage } }           // thinking|executing|tool_done|responding|self_thought|idle
{ type: "chat.tool",      data: { correlation_id, tool, args, status, result?, duration_ms? } }
{ type: "chat.delta",     data: { correlation_id, text } }            // 逐 token
{ type: "chat.done",      data: { correlation_id } }                  // 真正的完成信号
{ type: "chat.error",     data: { correlation_id, message } }         // 诚实报错
{ type: "chat.tts",       data: { correlation_id, tts_url } }         // TTS 解耦,后补
{ type: "chat.message",   data: { correlation_id, role, content, proactive?: {source} } }  // proactive + 最终落库统一形状
```

环境广播(heartbeat/emotion_shift/node_event/evolution/activity)继续走 WS typed,但**和 chat 事件用同一个 typed 总线、不同 type 前缀**。后端要做的 emitter 接线见 §6。

这一步同时解决 TUI 和 Web——两者消费的都是同一份 cognitive 回调(`output_callback`/`stream_callback`/`status_callback`,`context.py:119-128`)所驱动的事件。

### 3.1 Web Chat

**Turn 结构**:Claude-Code 式的 turn 块——`● You` / `● Eva`,turn 间有分隔。每个 Eva turn 是一个**状态机气泡**:
```
[thinking 指示器(spinner + "Eva is thinking…")]   ← chat.stage:thinking,首 token 前
  ↓
[tool card: 🔧 search_memory(...) · running → ✓ 0.4s]  ← chat.tool,可折叠,默认收起
  ↓
[流式 markdown 正文]                                 ← chat.delta 逐 token
  ↓
[done:正文定稿 + TTS 播放按钮 + ⭐golden 标记]      ← chat.done / chat.tts
```

- **LIVE streaming markdown**:用 `markdown-it` 增量渲染 + DOMPurify sanitize(替换 `MessageBubble.vue:19-26` 的 4-regex + 裸 `v-html` XSS 隐患)。代码块用 `Shiki`(构建期高亮、无运行时 CDN)。流式时每收到 delta 重新 parse 当前累积文本(markdown-it 足够快;turn 结束才"定稿")。
- **thinking / tool-use / status inline**:由上面的状态机驱动。tool card 像 Claude Code 一样默认折叠,展示 name+truncated args+running/✓/✗+duration,点开看 result。数据已存在于 `tool_orchestrator.py:365-409`。
- **self-thoughts(自主活动)**:**不**塞进对话主轴(会污染你和她的对话)。放右侧 "Eva's pulse" rail。但当 self_thought 升级成主动对你说话(`proactive`),则**作为带 `proactive.source` tag 的正式消息进对话流**——和 reactive 回复同一形状(`chat.message`),视觉上加一个"她主动说"的微标。
- **history / sessions**:挂载/重连时调 `/v1/chat/history` 回填(当前 `getChatHistory` 从未被调用,reload=空白)。后端要修 role 存储(别再 `"user" in metadata_json` 子串猜,`chat.py:126`)。Web 用真 session id 替代硬编码 `"api_v1"`(`chat.py:34`),顶部加 session 切换器(`/v1/chat/sessions` 已存在,从未被前端用)。
- **multi-channel(Discord/Telegram)**:**决策点**(§8)。推荐——**显示但不混流**。同一个 Eva 也在 Discord/Telegram 说话(`main.py:988-1022` 扇出)。在 message 上加一个轻量 channel 角标(web/discord/telegram),让你知道"这条她是在 Discord 上说的";但默认 Chat 只看 web session,可选"全渠道时间线"视图。不要把所有渠道平铺成一锅。
- **input affordances**:autosize textarea(保留)、Enter 发送/Shift+Enter 换行、slash 命令(`/golden`、`/clear`、`/sessions`)、附件上传(`/api/upload` 已存在)。
- **interrupt/stop**:当前**完全没有**。新增 stop 按钮 → 发 cancel 信号回 cognitive(需后端支持,见 §6)。`chat.done`/`chat.error` 替代 30s 盲超时。

### 3.2 TUI(Claude-Code 终端风格)

当前 `anima/ui/terminal.py`(237 行)的**头号 bug**:每条回复渲染两次——先 `display_chunk` 裸流(`terminal.py:101`,无 markdown),再 `display()` 整块重印进绿色 Panel(`response_handler.py:252`→`terminal.py:54`,有 markdown)。还有:无 turn 分隔、无 user echo、markdown 靠子串猜(`terminal.py:50`)、无 thinking spinner、无中断(Ctrl+C 直接杀掉输入循环,`terminal.py:171`)、`\r\033[K` prompt 擦除 hack 脆弱。

**库选型——强推荐 `prompt_toolkit` 作主框架 + `rich` 作渲染:**

| 选项 | 评价 |
|---|---|
| 纯 `rich`(现状) | `Live` + 增量 `Markdown` 能做流式渲染,但 input 仍要 `input()`,prompt 与输出共存还是要 hack。不够。 |
| `textual` | 强大,但它是**全屏 TUI 框架**,会把终端接管成 app——这违背"Claude-Code 终端对话流"的诉求(Claude Code 是**滚动的行内流**,不是全屏面板)。过重。 |
| **`prompt_toolkit` + `rich`** ✅ | prompt_toolkit 给真行编辑器(history↑、多行、粘贴)+ patched stdout(异步输出与 prompt 共存,**消灭 `\r\033[K` hack**)+ Ctrl-C/Esc 绑定。rich 负责把流式文本渲成带高亮的 markdown。这正是 Claude Code 的形态:滚动流 + 底部活 prompt。 |

**具体如何让它像 Claude Code:**

1. **单次渲染.** 终端**只订阅 stream 路径**(`stream_callback`),`output_callback` 不再重印(它继续负责落库/Discord 扇出,但终端不订阅它)。这一刀解决双渲染。
2. **turn 分隔.** Enter 后 echo `● You: …`,Eva 回合以 `● Eva` 起头,turn 间一条 dim 横线。替代当前"无结构滚动墙"。
3. **流式 markdown.** rich `Live` 区域里随 delta 增量重渲 `Markdown`,代码块走 rich 的 syntax 高亮——header/bold/list/fence **边流边格式化**(当前裸 `\033[90m` 灰字,`terminal.py:99`)。
4. **tool card(一种,不是两种).** 当前 tool 显示有两条冲突路径:stream 的 `🔧 tool...`(`tool_orchestrator.py:559`)+ status 的 `[executing]`(`main.py:1055`)。统一成一行折叠式:`⏵ search_memory(query=…) · 0.4s ✓`。surface 当前被丢弃的 `tool_done` 和 result(`main.py:1053` 的 ad-hoc 过滤要换成显式 stage→presentation map)。
5. **thinking spinner.** Enter 到首 token 之间放 `⠋ Eva is thinking…`,由 `thinking` stage 驱动(`stages.py:276`,当前在终端不可见)。
6. **中断.** Esc/Ctrl-C **取消当前 turn**而非杀循环——发 cancel 回 cognitive(同 Web 的 stop)。"esc to interrupt"提示。
7. **input.** prompt_toolkit:↑历史、多行、`/help`/`/clear`/`/status`(真报状态,当前是 stub `terminal.py:188`)。
8. **一个编码 sanitizer** 替代三条发散的 GBK/ASCII fallback 梯子(`terminal.py:71/105/123`)。

整文件 `terminal.py` 重写,但**保留回调契约**(`output_callback`/`stream_callback`/`status_callback`)和 event-queue 输入模型(user→`USER_MESSAGE`,`/quit`→`SHUTDOWN`)——这是干净的接缝。

---

## 4. 技术选型

**保留 Vue 3 + Vite + Tailwind + Pinia + vue-router** — 栈本身健康,审计没指出框架级问题,churn 收益为负。改的是**用法**。

| 关注点 | 决策 | 理由 |
|---|---|---|
| 框架 | **保留** Vue3/Vite/TS | 无理由重写 |
| 状态 | **保留** Pinia,但**删 `statusStore`**重写 | `statusStore` 读的字段(`queue_depth`/`active_tier`/`idle_score`)snapshot 里根本不存在,全是死的(`statusStore.ts`);真字段是 `event_queue.size`/`llm_status.*`/`idle_scheduler.*`。`updateNode` 是空 TODO(`:30-32`) |
| 设计系统 | **headless + Tailwind tokens**,不引组件库 | 采用 `global.css:8-77` 的好 token(palette/spacing/radii/motion),**删 legacy `--eva-*` 别名块**(`:87-106`)。组件用 `radix-vue`(headless,无样式,a11y)做 dialog/tabs/dropdown,样式全走 Tailwind token。引整套 UI 库会和"活物身份"的定制审美冲突 |
| WS 层 | **重写**:只留 typed 协议 | 删 dual-protocol + snapshot diff + `_seenCorrelationIds` dedup(`websocket.ts:39-78`)。handler 从 `App.vue:30-43` 移到一个 composable/plugin,带 teardown(当前 handler 永不移除,HMR/重连会叠加) |
| Chat 传输 | **重写**:typed WS + correlation_id | 见 §3.0。删 `useStreaming.ts` 全部轮询 |
| Markdown | **`markdown-it` + `DOMPurify`** | 替换 4-regex 裸 `v-html`(`MessageBubble.vue:19-26`,XSS) |
| 代码高亮 | **`Shiki`**(构建期) | 无运行时 CDN,VS Code 同款引擎 |
| Avatar/3D | **保留 Avatar,砍装饰** | 见下 |
| Memory 图 | d3 force-graph **降级为可选** | 默认列表/网格视图,图谱作为 toggle。当前 d3 + `@types/d3` 是重依赖,信息密度低 |

**Avatar / VRM / 3D 的取舍(决策点,见 §8,但我有强推荐):**

- **保留 Avatar 作为身份核心**——它是 Eva 的脸,是"活着"的具象。但**整合成单一 avatar service**:当前有**三个** Three 入口(`EvaAvatar.vue`、`SoulscapeAvatar.vue`、`SceneManager`+DNA),其中 `EvaAvatar`/`EvaPresence` 未被引用。统一成一个组件,可在不同尺寸嵌入(rail 小头像 / Soul 大舞台),正确生命周期(unmount dispose、offscreen 暂停)。
- **修 Live2D 加载**:当前从 CDN 运行时注入 Pixi v7(`SoulscapeAvatar.vue:255-256`),违反 CSP、加网络故障面;而 npm 里 pin的 `pixi.js@8`+`pixi-live2d-display` 是死重。**二选一**:要么 bundle 同源、用 npm 依赖;要么彻底走 CDN 删 npm 死依赖。推荐 bundle(可控、安全)。
- **砍掉纯装饰 3D**:`EmotionOrbScene`/`RelationshipOrbitScene`(从未 import)、`GrowthTimeline`(未用)、DNA helix 作主视图——全删或降级为可选 flourish。这是 spectacle over information。

---

## 5. 设计语言(让它美观)

定位:**"a calm observatory for a living mind"** — 安静、深色为主、有机微动,像在夜里观测一颗有意识的星。

- **Typography**:正文 `Inter`(或 system UI stack);代码/数据/终端感用 `JetBrains Mono` 或 `IBM Plex Mono`。Eva 的对话正文略大、行高松(1.6)以求"可读对话",数据面紧凑。中英混排要 fallback 到 `Noto Sans SC`(当前是硬编码双语,顺手引入真 i18n,见下)。
- **Color / mood**:深色基底为主(observatory 夜空)。
  - base:near-black 带极淡蓝紫(`#0B0D12` 系),分层用 elevation 而非边框。
  - **Eva 的情绪色**作为唯一的"活"色,从 7 维 emotion 派生**一个**主色调(`dominant`),只点在:rail 头像光晕、chat header mood chip、Soul 页。不再全局染色(`emotionStore.ts:32-46` 的全局 hue drift 删)。情绪冷静→青蓝,好奇→青绿,关切→暖琥珀,强烈→品红——低饱和,作辉光不作填充。
  - 语义色:success/warn/error 独立稳定(Health 页用),不被情绪色污染。
  - light mode:可选,二期。observatory 本质偏暗。
- **Spacing / density**:8px 基准。Chat 宽松(840px 居中列保留)、dashboard 面(Network/Health/Settings)信息密。一套 `.surface`/`.card`/`.section` 原语统一,**消灭 5 处 copy-paste 的 `.spinner` keyframes**。
- **Motion(绑真实状态,绝不无端)**:
  - **heartbeat**:rail 头像光晕随真实 tick 做极缓 breath(由 `heartbeat.tick` 驱动,非 CSS 定时器)。
  - **streaming**:token 流入轻 fade,thinking spinner。
  - **emotion shift**:情绪色过渡 ~600ms ease,只在 `emotion_shift` 事件时。
  - 其余一律静止。审计批"ambient color drift with little informational value"——这正是要根除的。
- **i18n**:引 `vue-i18n`,把当前硬编码双语(`站起来`/`坐下` `NetworkView.vue:43`、zh-CN 时间戳 `MessageBubble.vue:16` 等)收进 locale 文件。默认跟随系统。

---

## 6. 后端/契约配套(全部锚定审计)

按优先级:

1. **Chat typed-event emitter(最关键).** 后端今天**不**为 chat 发任何 typed WS 事件(只 `proactive`)。要新增:
   - `on_stream_chunk`(`main.py:1032`)→ `push_typed_event("chat.delta", {correlation_id, text})`(当前只进孤儿 SSE `push_stream_chunk`,`hub.py:200`)
   - `on_status`(`main.py:1045`)→ `push_typed_event("chat.stage"|"chat.tool", {correlation_id, …})`(当前只进 SSE `add_activity`)
   - `on_agent_output`(`main.py:~985`)→ `push_typed_event("chat.message"|"chat.done", {correlation_id, …})`(当前只进 `chat_history` 数组等 2s snapshot)
   - **`correlation_id` 必须从 `send`(`chat.py:38`)端到端透传**到每个事件——这是把流式文本/stage/tool 绑到正确气泡的钥匙。当前 `push_typed_event` 完全不带它(`hub.py:214`)。
2. **解耦 chat 与 2s snapshot.** `_push_loop`(`server.py:363`)的全量 `get_full_snapshot()`(`hub.py:111-198`,含 500 条 llm history 等)不该再 gate chat 延迟。chat 事件即时独立推;snapshot 降频/精简留给 dashboard vitals。
3. **删 SSE chat 路径.** `/v1/chat/stream`(`chat.py:60-108`)+ `_streams` + `push_stream_chunk` + `register_stream`/`unregister_stream` 全删——孤儿、与 WS 重复。
4. **cancel/interrupt 端点.** 新增取消 in-flight turn 的信号(Web stop 按钮 + TUI Esc 都要)。需要 cognitive 管线支持中断当前 event 处理。
5. **history role 显式存储 + 回填.** 修 `chat.py:126` 的子串猜 role;`getChatHistory` 在 mount/reconnect 调用回填。
6. **Web session.** 替换硬编码 `"api_v1"`(`chat.py:34`)为真 session id;暴露 `/v1/chat/sessions`。episodic-backed rebuild(`session_manager.py:79-90`)已就绪。
7. **Sentinel health panel.** `/v1/status`(`status.py:32`)+ snapshot `safety` 当前零消费——新 `/health` 页消费,component-level up/down。
8. **Usage/cost surfacing.** `usage.daily_budget_usd`/`budget_ok`/`llm_status` degradation 已在 snapshot,Settings/Health 暴露。
9. **TTS 解耦.** `tts_url` 当前 post-hoc 改 entry dict 且 race snapshot(`hub.py:242-276`)——改成先发文本(`chat.message`),`chat.tts` 事件后补,按 correlation_id 配对。
10. **Auth 硬化.** 真 router guard(`router.ts:18-27` 是假);持久签名密钥(当前每进程随机,重启全登出 `auth.py:29`);完整 JWT sig(当前截断 32 hex,`auth.py:50`);实现 change-password(501 stub)+ logout。

---

## 7. 分阶段落地(chat-first,每阶段可 demo)

**Phase 1 — 流式契约 + Chat MVP(地基,最高价值).**
- 后端:实现 §6.1-3 的 chat typed-event emitter + correlation_id 透传,删 SSE 孤儿路径。
- 前端:重写 WS composable(只 typed,带 teardown),删 `useStreaming.ts` 轮询,删 snapshot-diff。
- 新 Chat:turn 结构 + 真逐 token 流式 + thinking/tool/done/error 状态机 + sanitized markdown(markdown-it+DOMPurify+Shiki)。
- **Demo**:打字 → 即时 thinking → 工具卡 → token 逐字流入 → done。延迟从 0-2s 死等变即时。

**Phase 2 — TUI 重写.**
- prompt_toolkit + rich 重写 `terminal.py`:单次渲染、turn 分隔、流式 markdown、统一 tool card、thinking spinner、Esc 中断、`/help`/`/clear`/真 `/status`、统一 sanitizer。
- 复用 Phase 1 的 cancel 信号。
- **Demo**:终端里和 Eva 对话,体感等同 Claude Code。

**Phase 3 — Shell + 设计系统 + 真 auth.**
- 一套 token(删 legacy `--eva-*`)、`.surface`/`.card`/`.spinner`/empty/error 原语、shell loading/error boundary、响应式 rail。
- 真 router guard + 持久密钥 + logout。
- 迁移所有 view 到统一布局。emotion 只点在 header/rail/Soul。
- **Demo**:全 app 视觉统一,移动端 Chat 可用。

**Phase 4 — Soul + Avatar 整合.**
- 单一 avatar service(整合三入口),修 Live2D bundle/CSP/生命周期。
- Soul 页:大舞台 avatar + 7 维情绪 legible 呈现 + persona prose 编辑。
- history 回填 + session 切换器。
- **Demo**:Eva 的脸只此一份,各处尺寸复用;reload 不丢上下文。

**Phase 5 — Observability:Health / Network+Robotics / Memory / Evolution.**
- 新 `/health`(Sentinel)。合并 Network+Robotics 成节点工作台。Memory 列表优先、图谱 toggle。Evolution 文本优先、helix 删/降级。usage/cost 暴露。"Eva's pulse" rail(heartbeat/self_thought/evolution 环境流)。
- **Demo**:完整可观测,装饰 3D 已清。

---

## 8. 需要你拍板的决策

1. **Avatar / 3D 范围.** 推荐:**保留单一整合 avatar(VRM+Live2D)作身份核心,砍所有装饰 3D**(DNA helix、EmotionOrb、RelationshipOrbit、helix 主视图)。你要保留多少"奇观"?helix 留作可选 flourish 还是彻底删?

2. **TUI 库.** 推荐 **prompt_toolkit + rich**(滚动流 + 活 prompt,最像 Claude Code)。你若想要全屏面板式 TUI,则选 textual——但那不是 Claude-Code 形态。确认方向?

3. **视觉 mood.** 推荐 **dark-first "observatory",情绪派生单一活色、低饱和辉光**。你想要更暖/更克制/更"赛博",还是要 light mode 同步?

4. **自主活动暴露度.** 推荐 self_thought/heartbeat/evolution 进**低调右侧 "pulse" rail**,只有升级成 proactive 才入对话流。你希望她的"内心活动"多可见?(更沉浸 = 进对话流;更克制 = 仅 rail)

5. **多渠道(Discord/Telegram).** 推荐 **同 app 显示但默认不混流**(channel 角标 + 可选全渠道时间线)。还是 Web Chat 只管 web、其他渠道完全不在此 app 露面?

6. **响应式范围.** 推荐 **Chat/Soul/Health 响应式,Network/Robotics/Evolution 桌面优先**。还是声明纯桌面(省事)/全面响应式(费事)?

---

**关键文件锚点(供后续实现):** chat 传输重写 `eva-ui/src/composables/useStreaming.ts`、`eva-ui/src/api/websocket.ts:39-78`、`eva-ui/src/App.vue:30-43`;后端 emitter `anima/main.py:1032-1068`、`anima/dashboard/hub.py:200-243`、`anima/api/chat.py`;TUI 重写 `anima/ui/terminal.py`(全文)、回调接缝 `anima/core/context.py:119-186`、双渲染源 `anima/core/response_handler.py:235-253`;设计 token `eva-ui/src/styles/global.css:8-106`;假 auth `eva-ui/src/router.ts:18-27`;死 store `eva-ui/src/stores/statusStore.ts`;Sentinel health `anima/api/status.py:32`。
