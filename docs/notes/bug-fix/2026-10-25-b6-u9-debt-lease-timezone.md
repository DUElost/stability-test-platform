# B6-U9：欠账租约远期时间的时区解码溢出

Status: implemented
Class: bug-fix

## Decision

按 [#3723 v1.7.23 §3.9](https://github.com/DUElost/stability-test-platform/issues/3723)
与 [#3793](https://github.com/DUElost/stability-test-platform/issues/3793) 实施。
基线为 `origin/main@64f5754955d21bd755094bdb7ab2d5fb4fbb00f8`。

`backend/services/device_return.py` 只改 `_DEBT_LEASE_EXPIRES_AT` 与相邻注释：
值为 `datetime(9999, 1, 1, tzinfo=timezone.utc)`，在 UTC−12 至 UTC+14
的会话时区中均可解码；租约判定与释放仍不读取该字段，远期占位语义保持。

新增 revision `u9b6d3793a01`，父 revision 为 main 的 `u8b6d3752a01`。
`upgrade()` 只执行一条 UPDATE，把 `device_leases.expires_at` 晚于
`9999-01-01 00:00:00+00` 的值归一化，不读行进 Python，不按状态或类型过滤。
重复执行幂等；`downgrade()` 为空操作，保留可解码的数据。

其余改动为 `backend/tests/` 下两个回归文件、根迁移测试与本 Note。
根 `tests/test_disarm_guard_migration_3752.py` 按
[v1.7.23 规划者裁定](https://github.com/DUElost/stability-test-platform/issues/3723#issuecomment-6397273534)
删去固定 head 的断言及闲置导入，往返目标改为
`upgrade u8b6d3752a01 → downgrade u3b6c7d8e9f0 → upgrade head`。
列存在性、nullable 与 JSONB 断言保持不变；该测试继续验证 U8 迁移本身。
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

v1.7.22 的根套件发现 U8 固定 head 与 U9 新 revision 冲突，曾按 §7
退回并中止全量运行（退出状态 2，已运行部分为 1 failed、1443 passed）。
v1.7.23 将该根测试纳入范围，原 `B6-U9` Execution 已恢复；原有 scope 全部保留。

本轮实现提交为 `7d779e3a85daef72cbf82458feed958436c6cba0`。
按修订后的方案独立运行根迁移测试：

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py tests/test_disarm_guard_migration_3752.py -q --tb=short
```

结果：**1 passed**，退出状态 0，真实 testcontainers PG 验证三步往返。

U9 a/b/c 与空库迁移使用前文同一命令在新实现上重跑，
结果为 **12 passed**，退出状态 0；旧常量的六项失败输出仍保留在前文。

既有回归重跑命令：

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_device_return_u3_3728.py backend/tests/services/test_init_device_return_cli_3728.py backend/tests/services/test_device_return_lock_order_3728.py backend/tests/services/test_reviewer_u3_r1_f1_f2_3728.py backend/tests/api/test_disarm_policy_u8_3752.py backend/tests/api/test_device_return_debts_u6_3731.py backend/tests/api/test_device_retirement_api_2962.py backend/tests/api/test_heartbeat.py -q --tb=short
```

结果：**137 passed**，退出状态 0。

完整根套件重跑命令：

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py tests/ -q --tb=short
```

结果：**2420 passed, 18 skipped**，退出状态 0；包括真实 promtool 场景层。
本轮没有中止或跳过根迁移测试，该用例既单独通过，也在完整根套件中通过。

快速门禁重跑命令：

```bash
env -i PATH="$PATH" PYTHONPATH=. XDG_RUNTIME_DIR="/run/user/$(id -u)" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -- ./scripts/project_python.sh scripts/run_gates.py check:quick
```

结果：**16 gates 完成**，退出状态 0。
`schema-at-head` 因没有数据库配置明确跳过；空库迁移由前述 testcontainers 用例独立验证，
未为消除跳过而读取生产配置。结果回填只修改本 Note，产品与测试源码保持该实现提交的内容。

- 精确文本对照确认根测试仅有裁定的两处修改，其余断言不变。
- `backend/agent/` 相对 main 为零 diff；`git diff --check` 与该根文件 Ruff 检查通过。
- Registry `drift --strict` 返回 1，仅提示其它三条历史 Execution 的 stale-cache advisory；
  当前 U9 无 scope drift，未修改其它 Execution。
- 当前 PR HEAD 的六项 required CI 结果以 GitHub checks 为准，本地结果不代签 CI。

## Revisit

本 PR 的交付终点为 Draft 与独立复核。激活按 #3723 §5 第 8 条推进：
合入后的完整 CI、控制面部署与生产生效证据属于后续执行者工作，当前均 pending。
若后续新增远期哨兵或改变租约时间字段用途，需要重新核对支持时区与真实读入口。
