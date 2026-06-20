<p align="center">
  <h1 align="center">✈️ Server Pilot</h1>
  <p align="center"><strong>One-command server control for <a href="https://github.com/openai/codex">Codex</a></strong></p>
  <p align="center">
    SSH execution · GPU monitoring · Training tracker · File transfer
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white" />
    <img src="https://img.shields.io/badge/Codex-Skill-green" />
    <img src="https://img.shields.io/badge/License-MIT-yellow" />
  </p>
</p>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🖥️ **SSH Commands** | Execute any command on your remote server |
| 📊 **GPU Monitor** | Real-time temperature, power, memory & utilization |
| 🏋️ **Training Tracker** | Auto-detect training processes with elapsed time & GPU memory |
| 💻 **System Overview** | CPU, memory, disk, load at a glance |
| 📁 **File Transfer** | Upload & download files via SFTP |
| 🔄 **Watch Mode** | Continuous monitoring with auto-refresh |
| 📋 **JSON Output** | Machine-readable output for scripts & automation |

## 📸 Demo

```
============================================================
  Server Status Report
============================================================

📊 GPU:
  🔥 GPU 0: NVIDIA vGPU-32GB
     Temp: 56°C  |  Power: 258W / 320W
     Util: 100%  |  VRAM: [███████████████████░] 95% 31180/32760 MB

🏋️ Training:
  PID 177850  |  Elapsed: 05:14:49  |  CPU: 108%  |  VRAM: 3976 MB
     python train_efficientnet_m.py --mode full_voting
  PID 189739  |  Elapsed: 04:04     |  CPU: 90%   |  VRAM: 27188 MB
     python train_r2plus1d_l4_3d_improved.py --num_classes 16 ...

💻 System:
  23:56:53 up 210 days, 10:58, load average: 5.77, 6.10, 5.31
  Mem: 755Gi total, 41Gi used, 53Gi free

============================================================
```

## 🚀 Quick Start

### 1. Install

Copy the `server-pilot` folder to your Codex skills directory:

```bash
# Clone the repo
git clone https://github.com/MSWEIMZ/server-pilot.git ~/.codex/skills/server-pilot
```

### 2. Configure

```bash
# Copy the example config
cp ~/.codex/skills/server-pilot/scripts/server_config.example.json    ~/.codex/skills/server-pilot/scripts/server_config.json
```

Edit `server_config.json` with your server info:

```json
{
  "host": "your-server.com",
  "port": 22,
  "username": "root",
  "password": "your-password"
}
```

### 3. Use

Just talk to Codex naturally:

> 🗣️ "Check my server status"
>
> 🗣️ "How's the GPU doing?"
>
> 🗣️ "Is my training still running?"

Or run directly from terminal:

```bash
# Full status report
python scripts/server_monitor.py

# GPU only
python scripts/server_monitor.py --gpu

# Training processes only
python scripts/server_monitor.py --train

# JSON output
python scripts/server_monitor.py --json

# Continuous monitoring (refresh every 30s)
python scripts/server_monitor.py --watch

# Custom interval
python scripts/server_monitor.py --watch --interval 60
```

## 🛠️ Remote Commands

```bash
# Run any command
python scripts/ssh_exec.py "nvidia-smi"
python scripts/ssh_exec.py "df -h && free -m"

# JSON output
python scripts/ssh_exec.py --json "uptime"

# Upload file
python scripts/ssh_exec.py --upload ./model.pt /root/autodl-tmp/

# Download file
python scripts/ssh_exec.py --download /root/logs/train.log ./train.log
```

## 📦 Requirements

- **Python** 3.8+
- **paramiko** — auto-installed on first run
- **Codex** (optional) — works standalone too

## 📁 Structure

```
server-pilot/
├── SKILL.md                          # Codex skill descriptor
├── README.md                         # This file
├── .gitignore
├── agents/
│   └── openai.yaml                   # Codex UI metadata
└── scripts/
    ├── ssh_exec.py                   # SSH command executor
    ├── server_monitor.py             # GPU & training monitor
    ├── server_config.json            # Your config (git-ignored)
    └── server_config.example.json    # Example config
```

## 🤝 Contributing

Issues and PRs welcome! Some ideas:

- [ ] Multi-server support
- [ ] Training loss/accuracy log parsing
- [ ] Slack / DingTalk alert on GPU overheat
- [ ] Web dashboard

## 📄 License

MIT
