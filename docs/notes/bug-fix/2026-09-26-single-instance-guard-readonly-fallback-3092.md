# 单实例守卫降级可见化：只读 flock 回退 + O_NOFOLLOW + 告警面（#3092）

Status: implemented
Class: bug-fix

## Decision

[#2961 的守卫](2026-09-21-agent-singleton-flock-guard-2961.md)失败方向是「打不开锁文件就放行」，
只在日志留一行 warning。代价是：同一台机器上的 systemd 实例与手工 `python -m agent.main`
可以**静默**叠加，回到 2026-07-27 心跳互拒事故的形态——守卫恰恰是在那台机器上失效的。
本单按 2026-09-26 裁决（方案 C + 残余告警）把这条路径拆成三档：

| 情形 | 行为 |
|---|---|
| 读写打开成功 | 原样：flock + 写 pid |
| 读写失败、只读成功（如 root 属主 0644） | **回退只读 flock**：守卫照常生效，只是不写 pid |
| 只读也失败（0600 root、符号链接、目录不可用） | 降级放行 + 降级事实进心跳/指标/告警 |

落点与口径：

- `startup_guards._open_lock_file()`：`O_CREAT|O_RDWR|O_CLOEXEC|O_NOFOLLOW` 失败后，
  退 `O_RDONLY|O_CLOEXEC|O_NOFOLLOW`——Linux 的 flock 对只读 fd 同样生效（本单裁决依据的实测），
  被占用时第二个进程照旧 `sys.exit(1)`。`O_NOFOLLOW` 同时消掉「跟随符号链接后被 ftruncate」
  的风险；只读路径本身没有截断动作。
- 降级事实：模块级 `_degraded_reason` + `single_instance_guard_degraded()`（进程级启动期常量，
  只在启动时判定一次）。
- 心跳：`compute_capacity(single_instance_degraded=...)` → `health.reasons` 增
  `single_instance_guard_degraded`（warning 级 → DEGRADED，**不参与** `health_limit`/槽位计算，
  与 `usb_fault_reasons` 同族）。
- 控制面：`_HEALTH_REASONS` 分桶 + 新告警 `StabilitySingleInstanceGuardDegraded`
  （warning / `for: 10m`）+ promtool 场景 + 前端 `REASON_LABELS`
  —— 走的是 #2900 建立的四处对拍词表，不新增并行通道。

**一次性判定是刻意的**：降级不可在线自愈，恢复出口 = 修好锁文件 + `systemctl restart`
（重启本就会重判）。不在本单引入后台重试/监视的第二套恢复语义；触发条件见 Revisit。

## Alternatives

- **无权限即拒绝启动（裁决方案 A）**：残留一个 root 锁文件就让整机 Agent 起不来，
  systemd 重启次数打满后需人工上机——代价高于它防的风险。已否决。
- **只告警不回退（方案 B）**：双实例仍可发生；只读 flock 在绝大多数权限场景下能让守卫
  继续生效，回退是本质修法，告警只留给残余情况（如 0600）。
- **pidfile / 往只读 fd 写 pid**：只读 fd 的写入必然失败；pid 只是诊断信息，
  不值得为它破坏只读语义。回退路径不写 pid 是已知取舍。
- **后台周期重试取锁 / inotify 监视锁文件**：能免重启自愈，但要新增线程与状态机，
  且恢复判据要在「拿到锁」与「已被占用」之间再分叉；现网还没有长驻降级主机的实例，
  先不做（Revisit 带触发器）。
- **把降级 reason 放进 `capacity` 段（如 #2957 的通道态）**：那是「传感器自身状态」的位置，
  但守卫降级是主机故障风险（双实例叠加），进 `health.reasons` 才能复用既有告警链；
  代价是页面显示 DEGRADED——与该 reason 的 warning 级语义一致。

## Verification

实跑命令与结果：

| 命令 | 结果 |
|---|---|
| `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python -m pytest backend/agent/tests/ -q`（CI 同款干净环境） | **2196 passed** |
| `pytest backend/agent/tests/test_agent_singleton_2961.py backend/agent/tests/test_capacity_reporter.py -q` | **52 passed** |
| `pytest tests/test_host_health_reason_surface.py tests/test_prometheus_alerts_contract.py tests/test_alert_count_claims_are_live.py -q` | **69 passed** |
| `promtool check rules deploy/prometheus/alerts-stability-platform.yml` | **SUCCESS: 40 rules found** |
| `promtool test rules deploy/prometheus/alerts-stability-platform.test.yml` | **SUCCESS** |
| `scripts/run_gates.py check:quick` | **[OK] check:quick (16 gates)** |
| `ruff check`（6 个改动 py 文件）+ `npx eslint ExpandableHostTable.tsx` | **All checks passed** |
| `npx vitest run src/components/network/ExpandableHostTable.test.tsx` / `npm run type-check` | **31 passed** / **通过** |

新增/改写的用例形状（对齐裁决的三条验收）：

- **只读回退**：锁文件 0644 且属主不可写（`chmod 0o444`，非 root 下 O_WRONLY 得 EACCES）→
  首次取锁成功、`single_instance_guard_degraded() is False`、二次取锁 `SystemExit(1)`、
  锁文件内容保持空（只读路径不写/不截断）；
- **残余降级**：`chmod 0o000` → 返回 `None` 且降级标志置位（原 `test_unavailable_lock_path_is_fail_open`
  同步从「只断言 fail-open」改成「fail-open + 降级置位」——只回退不置位会红）；
- **符号链接**：`agent.lock -> target` → 不跟随（链接目标内容逐字不变）、降级置位；
- **接线守卫**：AST 读 `heartbeat_thread` 源码，断言 `single_instance_degraded` 确实传给
  `compute_capacity`（失败形态是「判了但上不了报」，与 `test_kernel_usb_faults` 同思路）；
- **容量面**：reason 出现 + DEGRADED、槽位与 healthy 主机一致（不打闸）、默认不出现。

权限类用例带 `skipif(geteuid() == 0)`：root 无视模式位，EACCES 路径不可达（CI 以非 root 跑）。

## Revisit

- 现网出现第一台**长驻**降级主机（修不了锁文件属主、只能带病运行）时，评估在
  `reload_config`（已有热更新入口）里补一次重试取锁，把自愈窗口从「重启」压到「热更」。
- `for: 10m` 与 warning 级：上线后按第一次真实触发的误报/漏报回看（守卫降级是常量事实，
  预期要么不响、要么长响）。
- 与 #2957 的联动：wrapper 只读子命令落地后，「查/修锁文件」可能获得一条带审计的固定路径；
  届时评估是否把它写进本告警的处置文案。
