<p align="center">
  <h1 align="center">✈️ Server Pilot</h1>
  <p align="center"><strong>一键掌控远程服务器的 <a href="https://github.com/openai/codex">Codex</a> Skill</strong></p>
  <p align="center">
    SSH 命令执行 · GPU 实时监控 · 训练进度追踪 · 文件传输
  </p>
  <p align="center">
    <a href="README.md">English</a> | <a href="README_CN.md">中文</a>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white" />
    <img src="https://img.shields.io/badge/Codex-Skill-green" />
    <img src="https://img.shields.io/badge/License-MIT-yellow" />
  </p>
</p>

---

## ✨ 功能一览

| 功能 | 说明 |
|------|------|
| 🖥️ **SSH 命令执行** | 在远程服务器上执行任意命令 |
| 📊 **GPU 监控** | 实时温度、功耗、显存、利用率 |
| 🏋️ **训练追踪** | 自动发现训练进程，显示运行时间和 GPU 显存 |
| 💻 **系统概览** | CPU、内存、磁盘、负载一目了然 |
| 📁 **文件传输** | 通过 SFTP 上传和下载文件 |
| 🔄 **持续监控** | 自动刷新，实时追踪状态变化 |
| 📋 **JSON 输出** | 机器可读格式，方便脚本和自动化 |

## 📸 效果演示

```
============================================================
  服务器监控报告
============================================================

📊 GPU 状态:
  🔥 GPU 0: NVIDIA vGPU-32GB
     温度: 56°C  |  功耗: 258W / 320W
     利用率: 100%  |  显存: [███████████████████░] 95% 31180/32760 MB

🏋️ 训练进程:
  PID 177850  |  运行: 05:14:49  |  CPU: 108%  |  GPU显存: 3976 MB
     python train_efficientnet_m.py --mode full_voting
  PID 189739  |  运行: 04:04     |  CPU: 90%   |  GPU显存: 27188 MB
     python train_r2plus1d_l4_3d_improved.py --num_classes 16 ...

💻 系统资源:
  23:56:53 up 210 days, load average: 5.77, 6.10, 5.31
  内存: 755Gi 总计, 41Gi 已用, 53Gi 空闲

============================================================
```

## 🚀 快速开始

### 1. 安装

```bash
git clone https://github.com/MSWEIMZ/server-pilot.git ~/.codex/skills/my-server-ssh
```

### 2. 配置

```bash
cp ~/.codex/skills/my-server-ssh/scripts/server_config.example.json \
   ~/.codex/skills/my-server-ssh/scripts/server_config.json
```

编辑 `server_config.json`，填入你的服务器信息：

```json
{
  "host": "你的服务器地址",
  "port": 22,
  "username": "root",
  "password": "你的密码"
}
```

### 3. 使用

直接跟 Codex 对话即可：

> 🗣️ "帮我检查服务器状态"
>
> 🗣️ "GPU 现在怎么样？"
>
> 🗣️ "我的训练还在跑吗？"

或者在终端直接运行：

```bash
python scripts/server_monitor.py              # 完整报告
python scripts/server_monitor.py --gpu        # 只看 GPU
python scripts/server_monitor.py --train      # 只看训练进程
python scripts/server_monitor.py --json       # JSON 格式
python scripts/server_monitor.py --watch      # 持续监控（30秒刷新）
python scripts/server_monitor.py --watch --interval 60
```

> 💡 **Windows 用户提示：** 运行监控脚本前先执行 `chcp 65001` 避免 emoji 编码错误。

## 🛠️ 远程命令

```bash
python scripts/ssh_exec.py "nvidia-smi"
python scripts/ssh_exec.py --json "df -h"
python scripts/ssh_exec.py --upload ./model.pt /root/autodl-tmp/
python scripts/ssh_exec.py --download /root/logs/train.log ./train.log
```

## 📦 依赖

- **Python** 3.8+
- **paramiko** — 首次运行自动安装
- **Codex** （可选）— 也可以独立使用

## 📁 目录结构

```
server-pilot/
├── SKILL.md                          # Codex 技能描述
├── README.md                         # 英文文档
├── README_CN.md                      # 中文文档
├── .gitignore
├── agents/
│   └── openai.yaml                   # Codex UI 元数据
└── scripts/
    ├── ssh_exec.py                   # SSH 命令执行（上传/下载/运行）
    ├── server_monitor.py             # GPU / 训练状态监控
    ├── task_mgr.py                   # 后台任务管理（tmux/screen/nohup）
    ├── file_ops.py                   # 文件操作：cat、ls、编辑、搜索、同步、大文件续传
    ├── server_config.json            # 你的配置（已 git 忽略）
    ├── server_config.example.json    # 配置示例
    └── web/
        ├── dashboard.py              # Web 仪表盘后端
        └── dashboard.html            # 仪表盘前端（SVG 仪表、中英文、主题切换）
```

## 🤝 参与贡献

欢迎 Issue 和 PR！

- [x] 多服务器支持
- [x] 训练 loss/accuracy 日志解析
- [x] Web 仪表盘（GPU 仪表、训练追踪、系统概览、中英文切换）
- [x] 后台任务管理（tmux/screen/nohup 自动检测）
- [x] 文件操作（大文件断点续传）
- [ ] GPU 过热时推送钉钉/Slack 告警
- [ ] 一键部署服务器环境

## 📄 开源协议

MIT
