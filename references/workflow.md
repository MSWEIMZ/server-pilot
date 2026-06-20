# Workflow Guide

## Typical Scenarios

### 1. Check server before starting work
```
"Check my server status"
```
This runs the full monitoring report showing GPU, training processes, and system resources.

### 2. Monitor a running training job
```
"Is my training still running? Show me the progress"
```
This shows training processes with parsed epoch/loss/accuracy from logs.

### 3. Run a quick command
```
"Run nvidia-smi on my server"
```

### 4. Transfer files
```
"Upload model.pt to my server at /root/autodl-tmp/"
"Download /root/logs/train.log to my local machine"
```

### 5. Continuous monitoring
```
"Watch my server GPU every 60 seconds"
```

## Multi-Server Setup

Edit `scripts/server_config.json`:

```json
{
  "servers": {
    "gpu-box": {
      "host": "192.168.1.100",
      "port": 22,
      "username": "root",
      "key_file": "~/.ssh/id_rsa"
    },
    "train-server": {
      "host": "10.0.0.50",
      "port": 26628,
      "username": "root",
      "password": "mypassword"
    }
  }
}
```

Then specify which server:
```
"Check GPU on gpu-box"
"Run ls on train-server"
```

## SSH Auth Priority

1. Explicit `--key` or `--pass` flag
2. `key_file` or `password` in config
3. Default SSH keys (`~/.ssh/id_rsa`, `id_ed25519`, `id_ecdsa`)

## Safety Notes

- Passwords are stored in `server_config.json` which is git-ignored
- Destructive commands should be confirmed with the user first
- This skill does NOT modify remote files unless explicitly asked
- SSH key auth is recommended over password for security
