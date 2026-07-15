# A40 容器任务监控实施计划

## 1. 建立可测试的解析边界

- 在 `scripts/web/dashboard.py` 提取纯函数 `parse_myjobs_output`，负责 ANSI 清理后
  的用户、GPU、Processes、My Tasks 区块解析，并保留 `current_user`、`scope`、
  `data_source` 和 warnings。
- 新增单元测试，覆盖头部用户解析、分组任务解析、所有用户进程解析，以及缺少可选区块
  时仍返回有效结果。

## 2. 修正 A40 fallback 数据契约

- 提取 `training_from_myjobs`（或等价纯函数），输入 myjobs 进程记录，输出 Dashboard
  training 记录。
- 不再按 SSH 用户名筛选；保留原始进程用户，包含所有可见任务。
- 独立转换 RAM 为 `ram_mb`，把 `vram_mb`/`host_pid` 设为不可用值并添加容器 PID
  作用域和数据源字段。
- 新增单元测试，断言多用户任务不丢失且 RAM 不会进入 VRAM。

## 3. 接入轮询并补齐前端

- `myjobs_info` 使用解析函数；`do_poll` 在 nvidia-smi 进程为空时使用新的 fallback。
- 训练卡片按可用性显示 VRAM 或 `—`，同时显示 RAM/作用域。
- `renderMyjobs` 增加 My Tasks 卡片和容器范围提示；保留现有用户与全进程表。
- 增加 HTML 回归测试，确保 My Tasks 和真实性提示不会被删除。

## 4. 验证与交付

- 先运行新增测试确认红灯，再实现最小改动并运行 Python 全套测试、compileall 和
  Windows Pester 检查。
- 启动临时 Dashboard 查询 `dev-server`，确认 4 张 A40、myjobs 可用、training 为
  多用户进程数、My Tasks 有数据，且 fallback 行的 VRAM 为空而 RAM 有值；随后停止
  本次启动的临时进程。
- 只提交本功能分支，合并回 `main` 时不触碰主目录已有未提交文件。
