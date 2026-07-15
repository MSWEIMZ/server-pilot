# A40 容器任务监控设计

## 背景

A40 服务器通过容器提供运行环境。容器内的 `nvidia-smi` 可能返回宿主机 PID，
但这些 PID 不一定存在于当前 PID namespace；`myjobs` 则能看到容器内的进程和当前
用户任务。因此 Dashboard 需要同时展示可见范围内的真实数据，并明确标出无法从
容器内推导的字段。

## 目标

- 稳定执行容器用户的 `myjobs -d`，包括用户 PATH 不完整的情况。
- 解析并保留 `Users`、`Processes` 和 `My Tasks` 三个区块。
- 当 `nvidia-smi` 无法关联容器进程时，把 `myjobs` 的全部进程作为训练/任务数据源，
  不按 SSH 登录用户过滤。
- 保留进程原始用户、容器 PID、CPU 和 RAM；不能映射的宿主 PID、逐进程 VRAM 显示
  为 `null`/`—`，并携带 `pid_scope=container` 和数据源说明。
- 前端单独显示 “My Tasks”，同时显示容器作用域提示，避免把容器数据误解为宿主机
  全量数据。

## 非目标

- 不在本次改造中获取宿主 PID namespace 或宿主级逐进程显存。容器内没有足够权限时，
  只能通过后续安装宿主机采集器/代理来补齐。
- 不修改 SSH 安全策略、服务器配置或用户已有的实验产物。

## 数据契约

`myjobs_info` 返回：

- `scope`: `container`；
- `data_source`: `myjobs`；
- `current_user`: 从 myjobs 输出头部解析出的用户（无法解析时为空）；
- `gpus`、`users`、`processes`、`my_tasks`：按原始区块解析；
- `warnings`: 容器作用域和不可映射字段说明。

补充到 `training` 的每条记录保留 `user`、`pid`（容器 PID）、`ram_mb`，并设置
`vram_mb=null`、`host_pid=null`、`pid_scope=container`、`data_source=myjobs`。
已有可验证的 GPU 进程记录仍沿用原有显存字段。

## 前端行为

- Training 卡片在有 `vram_mb` 时显示逐进程 VRAM；没有时显示 `—`，并显示 RAM 和
  `container PID` 作用域，不再把 RAM 数值放进 VRAM 字段。
- Cluster Users 区块增加 My Tasks 分组卡片，展示 group/count/CPU/RAM。
- 容器数据提示显示在区块顶部，明确宿主 PID 与逐进程 VRAM 尚未从当前容器映射。

## 验收标准

1. 解析包含 ANSI 控制符的 myjobs 样例时，当前用户、所有用户进程和 My Tasks 均可读出。
2. A40 fallback 不丢弃 `wanghon+`、`licheng+` 等非 SSH 登录用户进程，且 RAM 不写入 VRAM。
3. 前端源码包含 My Tasks 渲染和容器字段可用性提示。
4. 现有测试、语法检查和一次真实 A40 Dashboard API smoke 检查通过。
