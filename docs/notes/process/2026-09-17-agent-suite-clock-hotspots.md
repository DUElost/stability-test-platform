# 加速 pr-agent-tests：修 agent 套件墙钟热点（假时钟/stub sleep）

Status: implemented
Class: process

## Decision

在**不修改**已发布 `backend/agent/scripts/*/v*/` 的前提下，只改测试侧时钟/ sleep
stub，消掉 `backend/agent/tests` 里几处墙钟等待热点，缩短 PR 门禁
`pr-agent-tests` 的 wall time。

具体四处：

1. **powercycle**：原先 `monkeypatch.setattr(mod.time, "sleep", lambda _: None)`
   只 noop sleep，但 `_wait_device_online` 用 `time.time()` 判 deadline → busy-wait
   整段超时。改为 `_patch_advancing_clock`：sleep 推进假时钟。
2. **monkey_test**：加载后的模块内 `time.sleep` 未 stub；在
   `test_monkey_test_stdout_contract` / `test_monkey_test_v120` 的准备路径上 noop。
3. **login_lockout**：模块经 `login_lockout.time.time` 读时钟，测试却
   `time.sleep` 真等。`_install_lockout_clock` 同时 patch 两边，保留
   `time.sleep(...)` 调用语义。
4. **scan_runner `test_queue_defers_until_configured`**：去掉固定 `sleep(2.5)`；
   将 runner 内长 sleep 压成 ~10ms，并用短 poll 等到 `pending_count()==1`。

不启用 xdist、不改 job 名；本轮只收墙钟浪费。

## Alternatives

- **改生产脚本 sleep/轮询**：违反已发布脚本不可变；且会改变现场行为，否决。
- **pytest-xdist 并行**：能再压 wall time，但引入隔离/顺序风险，本 PR 明确不做。
- **全局 freezegun / pytest-time**：依赖与侵入面更大；热点已定位，局部 patch 足够。

## Verification

```bash
env -i PATH="$PATH" HOME="$HOME" PYTHONPATH=. .venv/bin/python -m pytest \
  backend/agent/tests/test_powercycle_scripts.py \
  backend/agent/tests/test_login_lockout.py \
  backend/agent/tests/test_monkey_test_stdout_contract.py \
  backend/agent/tests/test_monkey_test_v120.py \
  backend/agent/tests/test_scan_runner.py::test_queue_defers_until_configured \
  -q --durations=20
# → 82 passed in 0.58s（修复前 powercycle 超时路径 alone 即约数十秒）

env -i PATH="$PATH" HOME="$HOME" PYTHONPATH=. .venv/bin/python -m pytest \
  backend/agent/tests/ -q --durations=15
# → 2126 passed in 109.22s（先前约 ~165s；目标 ≤~110s）
```

## Revisit

若全套件仍明显高于 ~110s，下一步再拆：`test_step_stall_detection` 等仍含真实
2–4s 墙钟的用例、或在隔离契约明确后评估 xdist；仍禁止动已发布脚本版本目录。
