# B6-U9：欠账租约远期时间的时区解码溢出

Status: implemented
Class: bug-fix

## Decision

按 [#3723 v1.7.22 §3.9](https://github.com/DUElost/stability-test-platform/issues/3723)
与 [#3793](https://github.com/DUElost/stability-test-platform/issues/3793) 实施。
基线为 `origin/main@64f5754955d21bd755094bdb7ab2d5fb4fbb00f8`。

`backend/services/device_return.py` 只改 `_DEBT_LEASE_EXPIRES_AT` 与相邻注释：
值为 `datetime(9999, 1, 1, tzinfo=timezone.utc)`，在 UTC−12 至 UTC+14
的会话时区中均可解码；租约判定与释放仍不读取该字段，远期占位语义保持。

新增 revision `u9b6d3793a01`，父 revision 为 main 的 `u8b6d3752a01`。
`upgrade()` 只执行一条 UPDATE，把 `device_leases.expires_at` 晚于
`9999-01-01 00:00:00+00` 的值归一化，不读行进 Python，不按状态或类型过滤。
重复执行幂等；`downgrade()` 为空操作，保留可解码的数据。

其余改动为 `backend/tests/` 下两个回归文件与本 Note。
心跳、欠账查询与人工放行入口保留原有查询形状；产品代码没有时区配置或新增环境变量，
`backend/agent/` 没有改动。PR 保持 Draft，独立复核、Ready 与部署由后续工作面处理。

## Alternatives

没有采用旧哨兵、裁剪查询字段或更改数据库/产品会话时区的做法；
本单按已裁定方案同时修正新增写入与历史数据。
迁移回退不恢复旧值，因为历史 RELEASED 行也可能被读取并发生解码溢出。

## Verification

测试通过项目 Python 入口与内存保护 runner 运行，输出确认
`memory.max=6442450944`、`memory.swap.max=0`；数据库全部为
testcontainers 的 `postgres:16`，没有读取生产凭据或连接生产库/接口。
非 UTC 夹具只对自己创建的测试连接指定时区，未更改共享数据库或产品设置。

### a. 旧常量的真实入口失败证据

在基线旧常量下，先经真实阶段 B `init_device_return.main(["--apply"])`
建立欠账租约，再分别调用 `open_debts_for_host_sync`、
`POST /api/v1/heartbeat` 与 `POST /api/v1/devices/{id}/return-override`。
三个入口各自在 Asia/Shanghai 与 Pacific/Kiritimati 下独立运行，避免首个失败掩盖其它入口。

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_device_return_timezone_3793.py -k non_utc -q --tb=short
```

退出状态 **1**，实际输出摘录：

```text
E   psycopg.DataError: timestamp too large (after year 10K): '10000-01-01 07:59:59+08'
E   psycopg.DataError: timestamp too large (after year 10K): '10000-01-01 13:59:59+14'
FAILED backend/tests/services/test_device_return_timezone_3793.py::test_initialized_debt_non_utc_entry_points[open_debts-Asia/Shanghai]
FAILED backend/tests/services/test_device_return_timezone_3793.py::test_initialized_debt_non_utc_entry_points[open_debts-Pacific/Kiritimati]
FAILED backend/tests/services/test_device_return_timezone_3793.py::test_initialized_debt_non_utc_entry_points[heartbeat-Asia/Shanghai]
FAILED backend/tests/services/test_device_return_timezone_3793.py::test_initialized_debt_non_utc_entry_points[heartbeat-Pacific/Kiritimati]
FAILED backend/tests/services/test_device_return_timezone_3793.py::test_initialized_debt_non_utc_entry_points[override-Asia/Shanghai]
FAILED backend/tests/services/test_device_return_timezone_3793.py::test_initialized_debt_non_utc_entry_points[override-Pacific/Kiritimati]
6 failed, 5 deselected, 2 warnings in 4.24s
```

### a/b/c. 修复后的有限验收

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_device_return_timezone_3793.py backend/tests/migration/test_debt_lease_expiry_3793.py -q --tb=short
```

结果：**12 passed**，退出状态 0。

- a：上述六个真实入口用例均通过；人工放行关闭最后一条欠账并释放租约。
- b：独立 testcontainers 空库完成 `alembic upgrade head`、schema 比对与 seed 身份核对；
  随后回退 U9，注入 ACTIVE/RELEASED × MAINTENANCE/JOB 四行旧值。
  升级后四行均为新值，普通 JOB 时间与恰好处于新边界的行不变；
  同 revision 重跑、空操作 downgrade、再次实际执行 upgrade 后的完整快照一致。
  迁移后快照通过 UTC+14 的 psycopg 连接读取。
- c：初始化；终态同步原地转换与无租约新建；终态异步原地转换与无租约新建；
  带欠账取消退役，六个分支均从 PG 重新读取并断言新值。

### 回归、门禁与 CI

- 匹配范围的既有回归：pending。
- 根 `tests/`：pending。
- `check:quick`：pending。
- 当前 PR HEAD 的六项 required CI：pending，不能用本地结果代签。

## Revisit

本 PR 的交付终点为 Draft 与独立复核。激活按 #3723 §5 第 8 条推进：
合入后的完整 CI、控制面部署与生产生效证据属于后续执行者工作，当前均 pending。
若后续新增远期哨兵或改变租约时间字段用途，需要重新核对支持时区与真实读入口。
