# SSH 主机密钥三档模式设计

## 目标

在不改变日常个人使用体验的前提下，为 Server Pilot 提供可按需选择的 SSH 主机密钥校验策略。

## 配置

使用 `host_key_policy` 字段，可设置在多服务器配置的 `defaults` 中，也可设置在单台服务器条目中；单台服务器值优先。未配置时默认 `relaxed`。

## 模式

- `relaxed`：自动接受主机密钥，仅用于可信个人环境，保持旧版连接体验。
- `accept-new`：首次连接将主机密钥写入 `scripts/known_hosts`；之后密钥变化被拒绝。
- `strict`：仅接受系统 known_hosts 或 `scripts/known_hosts` 中已有的主机密钥。

无效值必须在连接前报出明确错误。仪表盘的本机监听、令牌和 API 输入限制不受该开关影响。

## 验证

单元测试使用伪 Paramiko 客户端验证三种策略分别配置 AutoAdd、Warning/保存和 Reject 行为，并验证默认值和单服务器覆盖。不会为测试连接任何远程服务器。
