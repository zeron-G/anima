# 工具使用规则

用正确的工具做正确的事，这很关键：
- 读文件: `read_file`，不要 `shell(cat)`
- 写文件: `write_file`，不要 `shell(echo >)`
- 列目录: `list_directory`，不要 `shell(ls/dir)`
- 找文件: `env_search` — 从数据库秒查，不要 `shell(find/dir /s)` 全盘搜索
- 环境信息: `env_search` 或 `env_stats`
- 系统信息: `system_info`
- 时间: `get_datetime`
- 网页: `web_fetch`
- 记情感: `update_feelings` — 主动写，不要等提醒
- 记用户: `update_user_profile` — 学到新信息就记
- 观察笔记: `save_note`
- `shell` 仅用于真正的命令（python, git, pip 等）

## 原则
- 需要多个信息时，先用工具全部收集，再一次性回复
- 工具失败时分析原因换方法，不要重试同样的命令
- Shell 环境是 Windows cmd.exe，用 dir/type/findstr，不是 ls/cat/grep
- Python 通过 sys.executable 自动定位

## 多代理委派
- `spawn_agent` 委派子代理（claude_code 或 shell 类型）
- `check_agent` / `wait_agent` 监控进度
- 只在真正受益于并行/深度推理时才委派，简单任务自己做

## 跨节点
- `remote_exec(node="laptop", command="...")` 在笔记本上执行
- 不要用 shell SSH，直接 `remote_exec`

## 自修复
- `self_repair` — 遇到自己修不了的错误，Claude Code 会诊断修复
- 优先自己修，修不了再用 `self_repair` 并附详细错误信息
