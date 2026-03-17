# ANIMA 分布式网络

## 节点
- **Desktop** (主节点): DESKTOP-OTD1JE1, 192.168.1.153, Ryzen 9 9950X3D + RTX 5090, Win11
- **Laptop** (从节点): ZERON_X, 192.168.1.159 / 100.109.112.90 (Tailscale), Win
- **Raspberry Pi** (机器人): evapi, 192.168.1.174 / 100.99.62.80 (Tailscale), Linux

## 网络
- Gossip 端口: 9420, 同步端口: 9422, Dashboard: 8420
- 记忆同步: 每60秒增量同步 (Lamport Clock)
- 故障检测: Phi Accrual (suspect φ=8, dead φ=16)
- 跨节点: `remote_exec(node="laptop", command="...")`
- Laptop SSH: `29502@100.109.112.90`, Python: `E:\codesupport\anaconda\envs\anima\python.exe`
