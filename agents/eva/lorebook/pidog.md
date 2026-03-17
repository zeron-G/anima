# PiDog Eva — 机器人肉身

- **硬件**: Raspberry Pi 5 (16GB) + PiDog 四足机器人，银色
- **地址**: 192.168.1.174 (LAN) / 100.99.62.80 (Tailscale)
- **SSH**: `eva@192.168.1.174` (密钥认证)
- **服务**: PiDog 控制 port 8888, EvaStation port 8080, 语音守护进程
- **代码**: `/home/eva/pidog_eva/` + `/home/eva/eva_station/`
- **GitHub**: `zeron-G/pidog-eva` (私有)
- **状态**: EvaStation v2 已部署, 语音系统工作中, 步态系统(layer0+layer1)重写完成, 14个单元测试通过
- **操作**: 用 `remote_exec` 或 SSH 控制，不要用 shell 工具直接连接
