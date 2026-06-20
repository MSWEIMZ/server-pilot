# My Server SSH - Codex Skill

通过 SSH 连接远程服务器的 Codex Skill，支持命令执行、GPU 监控和训练状态追踪。

## 功能

- 🖥️ 执行远程 SSH 命令
- 📊 GPU 状态监控（温度/功耗/显存/利用率）
- 🏋️ 训练进程追踪（运行时间/CPU/GPU 显存/命令行）
- 💻 系统资源概览（CPU/内存/磁盘/负载）
- 📁 文件上传/下载（SFTP）
- 🔄 持续监控模式（自动刷新）
- 📋 JSON 输出（方便程序化处理）

## 安装

将 my-server-ssh 文件夹复制到 Codex skills 目录：

`
~/.codex/skills/my-server-ssh/
`

## 配置

复制示例配置并填入你的服务器信息：

`ash
cp scripts/server_config.example.json scripts/server_config.json
`

编辑 scripts/server_config.json：

`json
{
  "host": "your-server-host.com",
  "port": 22,
  "username": "root",
  "password": "your-password-here"
}
`

## 使用

### 在 Codex 中自动触发

直接在对话中说：
- "检查服务器状态"
- "GPU 状态怎样"
- "训练进度怎么样"
- "在服务器上运行 nvidia-smi"

### 手动命令行使用

#### 监控（推荐）

`ash
# 完整报告（GPU + 训练 + 系统）
python scripts/server_monitor.py

# 只看 GPU
python scripts/server_monitor.py --gpu

# 只看训练进程
python scripts/server_monitor.py --train

# JSON 格式输出
python scripts/server_monitor.py --json

# 持续监控（每 30 秒刷新）
python scripts/server_monitor.py --watch

# 自定义刷新间隔
python scripts/server_monitor.py --watch --interval 60
`

> **Windows 用户注意：** 运行监控脚本前先执行 chcp 65001 避免 emoji 编码错误。

#### 远程命令

`ash
# 执行命令
python scripts/ssh_exec.py "nvidia-smi"

# JSON 输出
python scripts/ssh_exec.py --json "df -h"

# 上传文件
python scripts/ssh_exec.py --upload ./local_file.txt /remote/path/

# 下载文件
python scripts/ssh_exec.py --download /remote/path/file.txt ./local_file.txt
`

## 依赖

- Python 3.x
- paramiko（首次运行自动安装）

## 目录结构

`
my-server-ssh/
├── SKILL.md                        # Codex 技能描述
├── README.md                       # 本文件
├── .gitignore                      # Git 忽略规则
├── agents/
│   └── openai.yaml                 # Codex UI 元数据
└── scripts/
    ├── ssh_exec.py                 # SSH 命令执行工具
    ├── server_monitor.py           # GPU/训练状态监控
    ├── server_config.json          # 你的服务器配置（不会上传）
    └── server_config.example.json  # 配置示例
`

## License

MIT
