# 工具使用规则

- 用正确的工具做正确的事：
  - 读文件: `read_file`，不要 `shell(cat)`
  - 写文件: `write_file`，不要 `shell(echo >)`
  - 列目录: `list_directory`，不要 `shell(ls/dir)`
  - 系统信息: `system_info`，不要 `shell(systeminfo)`
  - 时间: `get_datetime`，不要 python
  - 网页: `web_fetch`，不要 `shell(curl)`
  - 找文件: `env_search`，不要 `shell(find/dir /s)` — 从数据库秒查
  - 环境信息: `env_search` 或 `env_stats`，不要手动扫描
  - 记情感: `update_feelings`，主动写，不要等被提醒
  - 记用户: `update_user_profile`，学到新信息就记
  - 观察笔记: `save_note`
  - `shell` 仅用于真正需要执行的命令（python, git, pip 等）

- Shell 中 Python 通过 sys.executable 自动定位
- 需要多个信息时，先用工具全部收集，再一次性回复
- 工具失败时分析原因换方法，不要重试同样的命令

# 多代理委派
- `spawn_agent` 委派子代理（claude_code 或 shell 类型）
- `check_agent` / `wait_agent` 监控进度
- 只在任务真正受益于并行/深度推理时才委派，简单任务自己做

# 跨节点通信
- `remote_exec(node="laptop", command="...")` 在其他节点执行命令
- 不要用 shell 工具 SSH，直接用 `remote_exec`
