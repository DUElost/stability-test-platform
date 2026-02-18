# ADR-0002: 单进程后端 + 内置后台调度线程
- 状态：Accepted
- 日期：2026-02-18
- 决策者：平台研发组
- 标签：调度, 线程模型, 部署约束

## 背景

当前平台需要同时运行任务分发、超时回收、工作流推进、Cron 触发等后台能力。为保证 MVP 快速落地，需优先降低部署复杂度。

## 决策

在 FastAPI 进程启动时直接拉起后台线程：

- `dispatcher`：扫描 PENDING 任务并派发。
- `recycler`：回收超时 run、释放设备锁、处理心跳超时。
- `workflow_executor`：推进多步骤工作流。
- `cron_scheduler`：按 cron 创建任务。

由于这些线程在进程内启动，生产 MVP 强制单实例后端运行，避免多实例重复调度。

## 备选方案与权衡

- 方案 A：独立调度服务（独立进程/服务）。
  - 优点：天然支持横向扩展与职责隔离。
  - 缺点：运维成本高，MVP 周期长。
- 方案 B：当前方案（进程内线程）。
  - 优点：部署简单，代码路径短。
  - 缺点：水平扩展受限，缺少 leader election。

## 影响

- 正向影响：快速形成闭环，便于本地/预发布演练。
- 负向影响：后端扩容不能直接加 worker；多进程会引入重复执行风险。

## 落地与后续动作

- 已落地：启动钩子内拉起四类后台线程。
- 后续：重构为“调度作业服务 + 选主机制”时，需新增替代 ADR 并将本 ADR 标记为 `Superseded`。

## 关联实现/文档

- `backend/main.py`
- `backend/scheduler/dispatcher.py`
- `backend/scheduler/recycler.py`
- `backend/scheduler/workflow_executor.py`
- `backend/scheduler/cron_scheduler.py`
- `docs/production-minimum-deployment-checklist.md`
