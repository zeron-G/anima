# ANIMA 重构进度与交接文档

> 本文件是"代码/状态分离"大重构的**单一事实源 + 跨机器交接说明**。
> 换工作环境后,`git clone` 本仓库 → 读本文件即可接着干。
> 最后更新:**Phase 4 工程化打磨(审计驱动)完成**——统一鉴权中间件、标准响应封装、`/v1/health`+`/v1/version`、输入校验、架构收口、`pytest` 0 失败、新增 `API.md`+`DEPLOYMENT.md` 并校准全部文档。**剩余:extras 拆分 + CI(见 §7)。**

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
| 灵魂的两半 | **人格种子**(初始 identity/rules,随内核发布)vs **人格实例**(进化后的 feelings/growth_log/persona_state/lorebook + 记忆库,属用户私有) | 进化引擎会改写要发布的代码,必须劈开(**Phase 2 已完成**) |
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

### ✅ Phase 2 — 数据外迁 + 人格种子/实例劈分
内核仓库从此**只发布种子**,活体灵魂私有化、永不回流。
- **种子/实例劈分**:新建 `agents/_seed`(identity / rules / examples / post_processing / config / manifest / soul + 出厂 persona 0.7 基线 + 空 feelings/growth/golden/lorebook)。`seed_agents_dir()` 指向它。
- **`anima init [--home X] [--name N]`**(`anima/bootstrap.py`):建 `data/` 骨架 → 从种子复制出活体 `home/agents/<name>` → 写 `config.yaml` + `.env`(从 example)。幂等(不覆盖已有实例,除非 `--force`)。`python -m anima init` 与 `anima init`(console script)共用同一 `handle_init`。
- **进化只动 home,不碰 git 跟踪代码**:活体实例(`agents/eva`,含 skills)+ 私有 data 残留(`env_catalog`/`issues/`/`user_profile.*.bak`)已 `git rm --cached` 并 gitignore(`/agents/*` + `!/agents/_seed/`),仍留磁盘 → 本机无缝运行;persona 自编辑写入 `agent_dir()`(已 gitignore),不再污染内核。
- **远程 git 同步可禁用**:新增 `evolution.git_remote_sync`(默认 **off**),统一门控所有 push/pull origin(main.py 自动 pull、engine `_deploy_via_pr` push、`_auto_rollback` force-push)。顺手删除无人调用的 `Deployer.deploy/rollback/_git` 死路径(清理残余路线)。
- **种子随 wheel 发布**:`setup.py` 把 `agents/_seed` 一并镜像进 `_resources`;`MANIFEST.in` 在 `prune agents` 后 `graft agents/_seed`(私有实例零泄漏)。
- **验收已通过**:① 干净 venv 装 wheel(`source_tree()=None`)`anima init` 从打包种子建出可加载的 home(identity/rules/persona/lorebook 齐全);② sdist/wheel 无 `agents/eva`/doujin/`env_catalog`/`.bak`;③ `pytest -q` 零回归(456 passed)。

### ✅ Phase 3 — 前端独立化
前端可连任意后端;后端无前端 dist 时仅 API 可用、不报错。
- **前端走 env**(本就大半就绪):`src/api/client.ts` 用 `VITE_API_BASE`、`src/api/websocket.ts` 用 `VITE_WS_BASE`(空=同源)。修正 `.env.production`(原硬编码 localhost → 同源默认 + 远程后端覆盖说明);`vite.config.ts` 代理目标改走 `VITE_DEV_PROXY`(默认本地)。
- **后端 `dashboard/server.py`**:① 托管路径 `dashboard.ui_dist` 可配(空→源码树 `eva-ui/dist`,装机无则 API-only);② SPA 由 8 条硬编码路由改 **catch-all `/{tail:.*}`**(排除 `/v1 /api /ws /static /desktop /assets`;未命中的 API 路径 404 而非吐 shell);③ **收紧 CORS**:由"反射任意 Origin + 凭证"改为 `dashboard.cors.allow_origins` 白名单(默认含 Vite dev 端口),`allow_all` 仅供 dev。
- **验收已通过**:dashboard 测试 8 passed(含新增 catch-all/CORS 2 个);前端 `npm run build` 通过;无 dist 时构造仅 API、无 catch-all、不报错;`pytest -q` 458 passed 零回归。

### ✅ Phase 4 — 工程化打磨(审计驱动)
5 维并行审计(API 契约 / 后端一致性 / 架构 / 文档 / 独立性)后按波次收口:
- **鉴权统一(critical)**:单一 `auth_middleware` 用 `api.auth.check_auth`(JWT over `dashboard.auth.password`)守卫 `/v1 /api /ws`,PUBLIC_PATHS 白名单。**修复分离部署下 WS 用裸 token 校验、与前端 JWT 不匹配导致实时流断开的致命 bug**,以及"设了 password 没设 token 时 /api/* 全开"的越权。删除 ~60 处手抄 `check_auth` 块 + server 端 raw-token 方案。
- **标准化**:新增 `api/responses.py`(`ok/err` 封装 + `read_json/query_int`)+ error middleware(未捕获异常→统一 500、不泄漏 `str(e)`);新增公开 `GET /v1/health`、`/v1/version`(契约版本号,供分离前端探活/版本协商)。
- **健壮性**:robotics command/nlp/speak 用 `read_json` + 非空校验(脏输入→400 而非 500/打到硬件);未就绪子系统 500→503;version 改 `importlib.metadata`。
- **架构收口**:`soul_container`/`watchdog` 路径改走 `data_dir()`(不再 `Path(__file__)` 爬升);删死代码 `safe_project_path`;**修 `Scheduler.add_job` 同名返回失效旧任务的真 bug** + 测试数据隔离 → **`pytest` 460 passed / 0 failed**(终结历史遗留失败)。
- **文档**:新增 [API.md](API.md)(REST/WS 契约)+ [DEPLOYMENT.md](DEPLOYMENT.md)(同源反代 & 完全分离);校准 README(中英)、DEVELOPER_GUIDE、ARCHITECTURE(补部署/数据模型),DEVELOPMENT 标记历史快照。

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

## 5. ⚠️ 换机器 / 装机迁移清单(最重要)

Phase 2 后内核仓库**只含种子**;活体灵魂与数据**不在 git 里**。分两种场景:

### A. 全新实例(无既有灵魂)
1. `git clone https://github.com/zeron-G/anima.git && cd anima`
2. `pip install -e ".[dev]"`(装机场景用 `pip install .`)
3. `python -m anima init` —— 从种子建出 `home/agents/eva` + `data/` 骨架 + `.env` 模板(装机无源码树时种子取自 wheel 内 `_resources/agents/_seed`,源码树时取自 `agents/_seed`)。
4. 编辑 `<home>/.env` 填密钥 → `python -m anima --headless`。

### B. 迁移既有 Eva(带上她的灵魂)
`git clone` 拿不到下列**私有**资产,必须随身带(网盘/私有仓/U盘):

| 必带 | 位置 | 说明 |
|---|---|---|
| **活体人格实例** | `agents/<name>/`(如 `agents/eva/`) | **Phase 2 起已 gitignore**;feelings/growth_log/persona_state/lorebook/习得 skills 都在此 |
| **情景记忆库** | `data/anima.db` | 长期记忆,核心(本机约 325MB) |
| **向量库** | `data/chroma/` | 语义检索 + 文档 RAG |
| **进化记忆** | `data/evolution_memory.yaml`、`data/evolution_state.json` | |
| **密钥 / 机器配置** | `.env`、`<home>/config.yaml` | 参照 `.env.example` 重建或拷贝 |
| 其它运行态 | `data/node.json`、`scheduler.json`、`notes/`、`uploads/` 等 | 见 `.gitignore` |

落位两法:**方式A(沿用源码树)**——把 `data/` + `agents/<name>/` + `.env` 放进仓库根,不设 ANIMA_HOME,源码树优先自动接上;**方式B(私有目录,推荐长期)**——私有目录 `X` 下放 `data/`、`agents/<name>/`、`.env`、`config.yaml`,`set ANIMA_HOME=X`。

**已在 git 里(clone 即得)**:全部 `anima/` 代码、`config/`、`prompts/`、`skills/`、`agents/_seed`(种子,非活体)。

**验证**:`python -c "from anima import config as c; c.load_config(); print(c.db_path(), c.db_path().exists())"` 指向你的库且 True;`pytest -q`(预期 456 passed,1 个既有失败见 §6)。

---

## 6. 重要工程事实 / 坑

- **裸 `python` 在 Windows Bash 里可能是坏的**(Windows Store 占位符,exit 49,无输出)。真解释器是 conda `anima` 环境(Python 3.11.x);各机路径不同,换机器先 `where python` / `conda env list` 定位你的 anima 环境。
- **既有失败测试**:`tests/test_full_system.py::test_scheduler_fires_events`。已用 `git stash` 对比确认在重构前的基线上**同样失败**(`scheduler.get_due_jobs()` 受真实 `data/scheduler.json` 持久化 + 去重逻辑干扰的测试隔离问题),**与本重构无关**。修它属于独立工作。
- **不要随便 `python -m anima` 启动真实 agent** 来"验证":会触发 LLM 计费、gossip 网络、主动外联(message_user)等副作用。验证路径用非侵入式检查(load_config + db_path + 文件存在性)。
- 提交身份:本仓库历史用 `zeron <2950243695@qq.com>`(已在本仓 `git config` 设好)。

---

## 7. 未来工作(按优先级)

> **已完成:Phase 0 / 1 / 1.5 / 2 / 3 + Phase 4 工程化打磨 —— 详见 §3。**

### Phase 4 剩余(可选,非阻塞)
- 拆可选 extras:`anima[network]`/`anima[robotics]`/`anima[voice]` 瘦身核心(较大的打包改动,暂未做)。
- CI 跑 ruff+pytest(基线已 460 passed / 0 failed)。
- 小残留:`core/event_routing.py` 提示串硬编码 `agents/eva/...`(默认 eva 两模式经 `workspace_root()` 都对;多 agent 名时模板化);其余 `/v1` handler 的成功响应仍为历史 shape(已在 [API.md](API.md) 标注,`{ok,data}` 为前向标准)。

---

## 8. 架构速查(供后续定位)

- **启动**:`python -m anima` → `anima/__main__.py` → 默认 `anima/desktop/app.py:launch_desktop`(pywebview)。`anima/main.py:run()` 分阶段 init:core→robotics→llm→heartbeat→network→cognitive→dashboard→wire_callbacks。约 60 处全局 `set_xxx()` 注入,无 DI 容器。
- **内核必需**:core, llm, memory, tools, perception, emotion, models, utils, config。
- **可选(config.enabled 门控)**:network(gossip/zmq), robotics(pidog), voice, channels(discord/telegram/webhook), evolution, dashboard, spawn, mcp。
- **数据**(全经 `data_dir()`):`anima.db`(记忆/情绪/审计/llm_usage/env_catalog/static_knowledge)、`chroma/`(episodic+documents 两集合)、notes/uploads/issues/credentials、scheduler.json、node.json、evolution_memory.yaml。
- **灵魂**(`agent_dir()` = home/agents/eva):config.yaml + identity/(core/extended/personality/relationship) + rules/*.md + memory/(feelings/growth_log/persona_state/golden_replies) + lorebook/。由 `llm/prompt_compiler.py` 与 `memory/retriever.py` 加载。
- **前后端接口**:前端 Vue3+Vite+Pinia(`eva-ui/`),axios `VITE_API_BASE` + WS `/ws?token=`;后端 aiohttp `anima/dashboard/server.py`(端口 8420),REST 在 `anima/api/*`(auth/chat/soulscape/evolution/memory/network/robotics/settings)。
