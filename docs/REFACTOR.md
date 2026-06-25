# ANIMA 重构进度与交接文档

> 本文件是"代码/状态分离"大重构的**单一事实源 + 跨机器交接说明**。
> 换工作环境后,`git clone` 本仓库 → 读本文件即可接着干。
> 最后更新:**Phase 1.5 全部完成(T6 wheel 打包 + A5 装机优雅禁用)**;本机灵魂实例已设为权威源(persona/feelings 以本机为准,见 §5)。

---

## 1. 目标与全局架构

把 ANIMA 从"代码、配置、用户私有状态死耦合在一棵项目树里"重构成三层,**只发布内核 + 前端,本地自动链接私有数据**:

```
① anima 内核        发布(代码,零用户数据)   anima/ + config/default.yaml + 人格种子
② 前端 eva-ui       独立发布                  Vue3,API 地址全部走 env
③ ANIMA_HOME        私有(本地/网盘/私有仓)    data/ + agents/<name> 活体实例 + config.yaml + .env
```

**链接机制**:内核启动按优先级解析 `home_dir()` → 自动接上你的数据库与灵魂。本地 `set ANIMA_HOME=<私有目录>` 即可,无需改代码。

---

## 2. 已锁定的关键决策(含理由)

| 决策 | 选择 | 理由 |
|---|---|---|
| 仓库结构 | **暂留单仓**,先内部解耦,稳定后再拆物理仓 | 风险最低,先把边界理清 |
| `ANIMA_HOME` 解析顺序 | `$ANIMA_HOME` → **源码树**(dev 用自己的 ./data)→ `~/.anima` → 新建 ~/.anima | 源码 checkout 优先于 ~/.anima,保证开发零回退;装机才落 ~/.anima |
| 代码资产归属 | `config/` `prompts/` `skills/` `agents 种子` 锚定 `package_root()`,打进 wheel | "只发布内核"的前提 |
| 本地配置覆盖层 | 迁到 `home_dir()/config.yaml`(过渡期仍兼容 `local/env.yaml`) | 与数据/灵魂同处一地,整体可搬迁 |
| 独立客户端 | **删除 Tauri/Rust(eva-desktop)**,保留 pywebview 作桌面兜底 | pywebview 是进程内桌面壳,非独立客户端;Tauri 冗余且乱 |
| 灵魂的两半 | **人格种子**(初始 identity/rules,随内核发布)vs **人格实例**(进化后的 feelings/growth_log/persona_state/lorebook + 记忆库,属用户私有) | 进化引擎当前会改写要发布的代码,必须劈开(Phase 2 处理) |
| Phase 1 提交粒度 | T1–T5 一次性提交,T6 作后续 | 路径注入是完整自洽单元 |

---

## 3. 当前进度

### ✅ Phase 0 — 删除 Tauri/Rust 独立客户端(commit `9d2bf86`)
- 整删 `eva-desktop/`(唯一 Rust 代码)。
- 摘除 README(中英)、`.gitignore`、`server.py` CORS 注释、`eva-ui/src/composables/usePlatform.ts` 的 Tauri 通知分支(回退 Web Notification)、`SoulscapeAvatar.vue` 注释中的引用。
- `python -m anima` 默认桌面模式仍由 `anima/desktop/app.py`(pywebview)兜底,不受影响。

### ✅ Phase 1 — 路径可注入(commit `ac44fa4`,T1–T5)
重写 `anima/config.py` 路径层,所有用户状态路径可注入、可重定位。**16 文件 +346/−137,测试零回归。**

完成项:
- **T1** `config.py` 路径模型重写(见 §4 API)。
- **T2** 消除 7 处模块级常量路径(加载即固化 → 惰性函数):`core/reload.py`、`voice/tts.py`、`voice/bridge.py`、`evolution/memory.py`、`desktop/singleton.py`、`core/tool_selector.py`。
- **T3** 数据库路径参数化:新增 `config.db_path()`,`config/default.yaml` 的 `db_path` 改为空(由 data_dir 决定),移除代码中 `"data/anima.db"` 硬编码(仅文档串残留)。
- **T4** `data_dir()/agent_dir()` 消费方收口到 home(因解析器已重定向,消费方无需逐个改,已验证语义正确)。
- **T5** `project_root()` 三类归位:用户数据(uploads/logs)→ `data_dir()`;代码资产(skills/default.yaml)→ `config_dir()/skills_dir()`;工具 cwd → `workspace_root()`;gossip 进化同步加无 git 树优雅跳过(`main.py`)。

### ✅ Phase 1.5 — 装机可发布化(T6 wheel 打包 + A5 进化/spawn 装机禁用)
让内核以 wheel 发布(无源码树)时仍能解析只读资产。
- `setup.py` 增 `build_py` 钩子:构建期把 `config/ prompts/ skills/` 镜像进 `anima/_resources/`(单一事实源,**不在 git 里复制**;`anima/_resources/` 已 gitignore)。
- `MANIFEST.in`:graft 上述资产、prune `data/ agents/ local/ eva-ui/`(sdist 不含任何私有态/灵魂)。
- `pyproject.toml`:`[tool.setuptools.package-data] anima=["_resources/**/*"]`。
- **A4 已通过**:仓库外干净 venv 装 wheel → `source_tree()` 为 None、`config_dir()/default.yaml` 为 True,prompts/skills/profiles/tool_selection 全部解析到包内资源。
- **人格种子未打包(有意为之)**:`agents/` 现为活体实例(含私有灵魂),种子/实例劈分属 Phase 2;装机消费方(`anima init`)尚不存在。打包种子留到 Phase 2(用 `agents/_seed`)。

**A5 — 进化/spawn/self-audit 装机模式优雅禁用**:在特性入口判 `source_tree() is None` → 记日志并禁用,绝不在 site-packages 里跑 git。
- `evolution/engine.py::submit_proposal` 首句守卫(返回 `disabled: not a source checkout`);`core/self_audit.py` 构造期置 `_enabled=False`、`run_tier` 返回 benign disabled(passed=True 不误报 issue);`spawn/packager.py::create_spawn_package` 抛清晰 `RuntimeError`;`dashboard/hub.py::_get_git_info` 与 `core/response_handler.py::_maybe_trigger_reload` 装机时短路返回空/跳过。
- 低优先残留(装饰性/不触发,有意未改):`llm/prompt_compiler.py:938`、`memory/store.py:162`、`utils/path_safety.py:80`。
- **A5 已通过**:强制 `source_tree()=None` 时三大特性均优雅禁用;源码模式 `pytest -q` 零回归(456 passed,1 个既有无关 scheduler 失败)。

---

## 4. `config.py` 新路径 API(后续开发请用这些,勿再硬编码)

```python
package_root()   -> Path           # 内核代码所在(锚定 __file__)
source_tree()    -> Path | None     # git 工作树根;pip 安装时为 None(git/进化/spawn 须判 None)
home_dir()       -> Path            # 用户状态根:$ANIMA_HOME → 源码树 → ~/.anima → 新建
set_home(p)      / set_data_dir(p)  # 测试/嵌入注入钩子(传 None 清除)

data_dir()       -> Path            # = home_dir()/data(自动 mkdir)
agent_dir()      -> Path            # = home_dir()/agents/<agent.name> 活体人格实例
agents_dir()     -> Path            # = home_dir()/agents
db_path()        -> Path            # SQLite 路径解析(绝对直用;相对落 data_dir;兼容旧 "data/" 前缀)

config_dir()     -> Path            # 源码树 config/(dev)否则 package_root()/_resources/config
prompts_dir()    -> Path            # 同上,prompts
skills_dir()     -> Path            # 同上,skills(内置技能 = 代码资产)
seed_agents_dir()-> Path            # 人格种子(Phase 2 bootstrap 源)
workspace_root() -> Path            # 工具 cwd:source_tree() or home_dir()
project_root()   -> Path            # 【已弃用 shim】= source_tree() or package_root().parent;勿在新代码用
```

**配置加载顺序**:`config/default.yaml` → `config/profiles/*` → `agents/<name>/config.yaml` → `home_dir()/config.yaml` →(兼容)`local/env.yaml` → `.env`。

---

## 5. ⚠️ 换机器继续工作的迁移清单(最重要)

`git clone` 只能拿到**代码 + 人格定义**,以下东西**不在 git 里**,必须另行带过去,否则 Eva 会"失忆"或起不来:

| 必带 | 位置 | 说明 |
|---|---|---|
| **情景记忆库** | `data/anima.db`(~172MB) | gitignore;Eva 的长期记忆,核心 |
| **向量库** | `data/chroma/` | 语义检索 + 文档 RAG 索引 |
| **进化记忆** | `data/evolution_memory.yaml`、`data/evolution_state.json` | gitignore |
| **密钥** | `.env`(仓库根) | gitignore;参照 `.env.example` 重建,或拷贝 |
| 其它运行态 | `data/node.json`、`data/scheduler.json`、`data/notes/`、`data/uploads/` 等 | 见 `.gitignore` 15–104 行 |

**已在 git 里(clone 即得)**:`agents/eva/` 的 identity/rules/config/manifest/persona_state/golden_replies/growth_log/lorebook 索引(42 文件)、全部 `anima/` 代码、`config/`、`prompts/`、`skills/`。

**新环境启动步骤**:
1. `git clone https://github.com/zeron-G/anima.git && cd anima`
2. 准备 Python 环境(见 §6 解释器),`pip install -e ".[dev]"`
3. 把上表"必带"文件放到位:
   - **方式A(沿用源码树)**:直接把 `data/` 整目录和 `.env` 拷到仓库根 → 不设 ANIMA_HOME,源码树优先,自动用 `./data`。
   - **方式B(私有目录,推荐长期)**:把数据放到私有目录 `X`,`set ANIMA_HOME=X`(其下需有 `data/`、可选 `agents/`、`config.yaml`、`.env`)。
4. 验证:`python -c "from anima import config as c; c.load_config(); print(c.db_path(), c.db_path().exists())"` 应指向你的库且 `True`。
5. 跑测试基线:`pytest -q`(预期 455 passed,1 个既有失败见 §6)。

---

## 6. 重要工程事实 / 坑

- **裸 `python` 在本机 Bash 里是坏的**(Windows Store 占位符,exit 49,无输出)。真解释器是 conda 环境,本机为
  `E:/codesupport/anaconda/envs/anima/python.exe`(`ANIMA.bat` 里写的是 `D:\program\codesupport\...` 的 pythonw,按机器实际为准)。换机器后先 `where python` / 定位你的 anima 环境。
- **既有失败测试**:`tests/test_full_system.py::test_scheduler_fires_events`。已用 `git stash` 对比确认在重构前的基线上**同样失败**(`scheduler.get_due_jobs()` 受真实 `data/scheduler.json` 持久化 + 去重逻辑干扰的测试隔离问题),**与本重构无关**。修它属于独立工作。
- **不要随便 `python -m anima` 启动真实 agent** 来"验证":会触发 LLM 计费、gossip 网络、主动外联(message_user)等副作用。验证路径用非侵入式检查(load_config + db_path + 文件存在性)。
- 提交身份:本仓库历史用 `zeron <2950243695@qq.com>`(已在本仓 `git config` 设好)。

---

## 7. 未来工作(按优先级)

### Phase 1.5 — 装机可发布化(只服务 pip install 场景,当前工作流不依赖)
**✅ T6 打成 PyPI wheel — 已完成(详见 §3 Phase 1.5)**
- 构建期 `setup.py` build_py 把 `config/ prompts/ skills/` 镜像进 `anima/_resources/`(单一事实源,gitignore);`pyproject.toml` 已加 `[tool.setuptools.package-data] anima=["_resources/**/*"]`;`MANIFEST.in` graft 资产、prune 私有目录。
- **人格种子推迟到 Phase 2**(避免把活体灵魂打进发布包,且种子/实例尚未劈分)。
- 验收 A4 已通过(仓库外干净 venv,`config_dir()/default.yaml` 为 True)。

**✅ 进化/spawn/self-audit 装机模式优雅禁用 — 已完成(A5,详见 §3)**
- 已在特性入口加 `source_tree() is None` 守卫:`evolution/engine.py::submit_proposal`、`core/self_audit.py`(构造期 `_enabled`)、`spawn/packager.py::create_spawn_package`、`dashboard/hub.py::_get_git_info`、`core/response_handler.py::_maybe_trigger_reload`。
- 低优先残留(装饰性/不触发,有意未改):`llm/prompt_compiler.py:938`、`memory/store.py:162`、`utils/path_safety.py:80`。
- 验收 A5 已通过:强制装机模式三大特性优雅禁用,源码模式 pytest 零回归。

**至此 Phase 1.5 全部完成。** 下一步进入 Phase 2(数据外迁 + 人格种子/实例劈分)。

### Phase 2 — 数据外迁 + 人格种子/实例劈分
- 新增 `anima init [--home X]` 命令:生成 `$ANIMA_HOME` 骨架,把**人格种子**(`agents/_seed`,从现 `agents/eva` 提炼初始 identity/rules)复制成用户的活体 `agents/<name>`。`seed_agents_dir()` 解析器已就位。
- 进化引擎改为**只写 `$ANIMA_HOME/agents/<name>`**,绝不碰已安装的内核代码;`evolution/engine.py` 的 `git pull origin private` 改可配置/可禁用。
- 仓库内清理:`data/` 运行时残留、`agents/eva` 的实例态(留种子)。
- 验收:`anima init` 后空 home 能跑;进化只动 home,不动 git 跟踪的代码。

### Phase 3 — 前端独立化
- 前端后端地址全部走 env:`eva-ui/.env.production`(`VITE_API_BASE`/`VITE_WS_BASE`)、`vite.config.ts:14-25` 代理目标、`src/api/client.ts:3`、`src/api/websocket.ts:24`。
- 后端 `anima/dashboard/server.py`:前端托管路径改可配置 `dashboard.ui_dist`(默认兼容相对 `eva-ui/dist`);SPA 路由由硬编码 7 条改 catch-all 中间件(排除 `/v1 /api /ws /static /desktop`);收紧生产 CORS(当前 `Allow-Origin: *`)。
- 验收:前端可连任意后端;后端无前端 dist 时仅 API 可用不报错。

### Phase 4 — 打磨成熟项目
- 拆可选 extras:`anima[network]`/`anima[robotics]`/`anima[voice]`,核心瘦身。
- 修既有失败测试;CI 跑 ruff+pytest;更新 `docs/ARCHITECTURE.md` 与部署指南。

---

## 8. 架构速查(供后续定位)

- **启动**:`python -m anima` → `anima/__main__.py` → 默认 `anima/desktop/app.py:launch_desktop`(pywebview)。`anima/main.py:run()` 分阶段 init:core→robotics→llm→heartbeat→network→cognitive→dashboard→wire_callbacks。约 60 处全局 `set_xxx()` 注入,无 DI 容器。
- **内核必需**:core, llm, memory, tools, perception, emotion, models, utils, config。
- **可选(config.enabled 门控)**:network(gossip/zmq), robotics(pidog), voice, channels(discord/telegram/webhook), evolution, dashboard, spawn, mcp。
- **数据**(全经 `data_dir()`):`anima.db`(记忆/情绪/审计/llm_usage/env_catalog/static_knowledge)、`chroma/`(episodic+documents 两集合)、notes/uploads/issues/credentials、scheduler.json、node.json、evolution_memory.yaml。
- **灵魂**(`agent_dir()` = home/agents/eva):config.yaml + identity/(core/extended/personality/relationship) + rules/*.md + memory/(feelings/growth_log/persona_state/golden_replies) + lorebook/。由 `llm/prompt_compiler.py` 与 `memory/retriever.py` 加载。
- **前后端接口**:前端 Vue3+Vite+Pinia(`eva-ui/`),axios `VITE_API_BASE` + WS `/ws?token=`;后端 aiohttp `anima/dashboard/server.py`(端口 8420),REST 在 `anima/api/*`(auth/chat/soulscape/evolution/memory/network/robotics/settings)。
