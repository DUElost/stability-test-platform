# 生产控制面只读诊断

本文记录生产控制面进行临时只读诊断时的凭据来源和安全边界。部署步骤见
[`production-minimum-deployment-checklist.md`](../production-minimum-deployment-checklist.md)；
测试隔离要求见 [`../development/testing.md`](../development/testing.md)。

按症状排查（设备从 `adb devices` 消失、页面上「在线 vs USB」不一致）见
[`host-device-visibility-triage.md`](./host-device-visibility-triage.md)——四层判别
（内核/USB · 主机 adb server · 设备 adbd · 设备 USB 功能集）与各层的只读命令。

## 安全边界

- 优先只读查询；写操作必须通过代码、迁移和 PR 流程；
- 不得把密码、token、私钥、连接串或主机清单内容写入代码、文档、日志或 PR diff；
- 不得用生产数据库代替测试数据库；
- 不得在生产数据库上试跑迁移；
- `.env.backend`、`backend/.env` 和 Agent `.env` 的职责不同，不得互相代用。
- **`backend/.env` 里的 `AGENT_SECRET` 是陈旧值**：控制面与全部 Agent 实际使用的
  都是 `.env.backend` 的生产值，`backend/.env` 的旧值签发的 token 一律不被认——
  诊断 auth 问题时以 `.env.backend` 为准，不要被 `backend/.env` 的残留值误导。

## 凭据来源

| 用途 | 来源 | 约束 |
|---|---|---|
| Agent fleet SSH | `/home/debian13/hosts.ini` 的 `[android]` 与 `[android:vars]` | 清单是本地敏感文件；规模以当前内容为准 |
| Backend 数据库 | 仓库根 `.env.backend` 的 `DATABASE_URL` | 本机 PostgreSQL 可能就是生产 `stp`；只读 SELECT 优先 |
| 控制面管理员 | 仓库根 `.env.backend` 的 `STP_ADMIN_USER`、`STP_ADMIN_PASSWORD`、`AGENT_SECRET` | `backend/.env` 不是生产凭据源 |

控制面本机使用仓库 `venv/bin/python` 和 psycopg 3。需要调用管理 API 时，先从
`/api/v1/auth/token` 获取 token，并使用同一生产 env 源中的 `AGENT_SECRET`；
不要把解析出的值打印或持久化。

## 文件边界

以下文件或目录是本机状态，不进入 Git：

- `.env.backend`、`backend/.env`、`backend/agent/.env`
- `/home/debian13/hosts.ini`
- `opencode.json`
- 私钥、token 与 Harness 本地凭据配置

Claude Code 的项目设置只禁止修改部分凭据文件；允许读取是为了支持经授权的只读运维。
这不构成读取授权，仍须由当前 Requirement 明确需要。

## 状态机一致性核对（可复跑）

> 来源：2026-09-17 一次完整只读核对（**当次 10 项全 0**——这是日期快照，以当次现查为准）。
> 全 `SELECT`；连接串只从仓库根 `.env.backend` 的 `DATABASE_URL` 取、经环境变量传入脚本，
> **不打印不落盘**；用仓库 `venv/bin/python` + psycopg 3。建议连接开 `autocommit=True`：
> 一条查询报错会污染事务，后续查询会连带报 `InFailedSqlTransaction`（实测踩过）。

| # | 判据（SQL 主体） | 非 0 意味着 | 谁处理 |
|---|---|---|---|
| ① | `device_leases.status='ACTIVE'` 且其 `job_instance.status ∈ (COMPLETED,FAILED,ABORTED)` | 租约泄漏：终态 job 仍占着设备 | `device_lease_reconciler` 回收 |
| ② | `plan_run.status ∈ (SUCCESS,PARTIAL_SUCCESS,FAILED)` 且存在 `job_instance.status ∉ (COMPLETED,FAILED,ABORTED)` | 聚合漏判：run 已终态而 job 未落 | 聚合路径（`plan_run_aggregation`） |
| ③ | `plan_run` 终态但 `ended_at IS NULL` | 终态化不完整 | `_finalize_plan_run` 调用链 |
| ④ | `device_leases.status='ACTIVE'` 且 `expires_at < now() - interval '1 hour'` | 回收停滞（过期租约无人收） | 回收器 / 调度 |
| ⑤ | `device.status='BUSY'` 且无 `status='ACTIVE'` 的租约 | **悬挂占用**：用户侧表现为「设备一直忙」 | 对照 ①④；修完租约后设备应自动回落 |
| ⑥ | 非终态 job（RUNNING/PENDING）且 `coalesce(last_execution_heartbeat_at, started_at, created_at) < now() - interval '6 hours'` | 卡死作业（无心跳推进） | 重试/退出端点或回收器 |
| ⑦ | `plan_run.status ∈ (QUEUED,PRECHECK)` 且 `started_at < now() - interval '2 hours'` | 准入停滞（队列不推进） | `admission_pump` / 队列遥测 |
| ⑧ | `plan_run_artifact` 按 `(plan_run_id, storage_uri)` 分组 `HAVING count(*) > 1` | 登记幂等键破了（同一产物登记两次） | scan/upload 登记路径 |
| ⑨ | `plan_run_artifact.storage_uri` 为空或全空白 | 登记写坏（产物不可寻址） | 同上 |
| ⑩ | 设备状态分布 `SELECT status, count(*) FROM device GROUP BY 1` | 与租约表对照：**无 ACTIVE 租约时不应出现 BUSY** | 与 ⑤ 合看 |

**读法（重要）**：①④⑤ 在**机群空闲**（`device_leases` 里没有 ACTIVE 行）时恒为 0——那时它们
没有判别力，只有在**有活跃作业**时才有意义。判「空闲」的最快方式即 ⑩ 与租约状态分布
（`SELECT status, count(*) FROM device_leases GROUP BY 1`）合看：若只有 `RELEASED` 且设备无
`BUSY`，则整组核对是"空真"，应换个有作业的窗口再跑。

**已知不查**：`step_trace`/`device_log_event` 等的父行引用由外键约束保证，不重复核对；
NFS 目录与行的对应属保留期清理面（见 `2026-storage-roles-and-aliases.md`）。
