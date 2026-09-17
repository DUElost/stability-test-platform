# pr-agent-tests 保核验压墙钟（stall 快轮询 + 同 job 并行）

Status: implemented
Class: process

## Decision

在**不削弱核验目的**的前提下压缩 `pr-agent-tests` 墙钟，遵循既定红线：
只压与断言无关的空转 / 编排；会削弱「拦什么」的改动不做（不删用例、不上
xdist、不改已发布脚本、不把 stall 改成 mock 子进程）。

两处落地：

1. **`test_step_stall_detection`**：仍用真实子进程 + `_pump_process`。仅把测试侧
   `_POLL_INTERVAL_SECONDS` 压到 50ms（生产仍为 1.0，只定精度），并同比缩短
   sleep / `stall_seconds` / `wall_clock`。断言集合不变（stall / 活锁 / PROGRESS /
   wall_clock / 线程回收）。
2. **`ci.yml` `pr-agent-tests`**：`tests/` 离线子集与 agent 套件在同 runner **并行**；
   agent 行仍 `env -i`（#2428）。「Run repo-level tests」step 名保留，改为复核并行
   exit code + 保留 `--ignore=` 文本锚点（`offline_subset_guard` 等），**不重跑**。

`tests/test_agent_env_selfsufficiency.py` 同步改为锚定 `pr-agent-tests` job 内的
multiline step（避免误匹配夜间 `backend-test` 同名 step）。

## Alternatives

- **缩短 stall 阈值但不改 poll**：生产 poll=1.0s，阈值压到亚秒会 flaky；否决。
- **假时钟 / mock `_pump_process`**：文件开宗明义要真管道与真杀树；否决。
- **拆第二个 required check**：要改 branch protection；本轮用同 job 并行避免。
- **pytest-xdist**：隔离风险未论证；按红线不做。

## Verification

```bash
env -i PATH="$PATH" HOME="$HOME" PYTHONPATH=. .venv/bin/python -m pytest \
  backend/agent/tests/test_step_stall_detection.py -q --durations=15
# → 32 passed；修复前 ~45s，修复后 ~6s

TESTING=1 JWT_SECRET_KEY=ci PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_agent_env_selfsufficiency.py \
  tests/test_offline_subset_guard.py \
  tests/test_ci_promtool_scenario_gate.py -q
# → 全绿
```

预期 CI（合入前 step 实测，run 35186879331）：agent 110s + repo 62s 串行。
并行后关键路径 ≈ max(agent, repo)；stall 文件本地 45s→6s，agent 步再少约数十秒。
总期望：`pr-agent-tests` 从 ~3.4min 再明显下探（setup 未动）。

## Revisit

若并行后日志交错影响排障，可改为 `pytest -q --color=no` 分文件落盘（已落
`repo.log`）。setup（双 Python + pip）仍是大头时另开编排专项。
