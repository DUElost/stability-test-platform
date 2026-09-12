# gpu_check v1.0.7：崩溃循环提前判失败（#831）

Status: implemented
Class: bug-fix

## Decision

未到 `GPU_RUN_END` 的崩溃循环此前不可见：loop 脚本与 `am` 客户端始终存活 →
`dead_streak` 永不触发；END 后判定（v1.0.3–v1.0.6 的 no-tests/crashed/failed
分支）只在 700 轮全部跑完后才可达——期间每周期报 success 且 `rounds_done`
上涨，平台视角「测试在推进」，可空转数十分钟到数小时（v1.0.3 实测空跑即此
形状）。

v1.0.7 每周期对日志取证，三类证据任一命中即当周期失败（不等 END）：

1. `Process crashed`（instrument 崩溃，文本判定不依赖 END）；
2. 最后一个 `OK (0 tests)`（空跑——取最后一个而非首个：中途单轮空跑后恢复
   不误杀）；
3. 最近连续 `crash_round_streak`（默认 3，可配，0=关闭）轮 `GPU_ROUND rc<0`
   （每轮崩溃、测试未推进）。

附带重构：`_run_finished(log=None)` 接受调用方已读日志——同周期只 `cat`
一次 test_log，避免新增一轮 adb 全量读。

## Alternatives

- 维持「END 后判定」——否：空转窗口本身即缺陷（issue B 级定义）。
- `streak=1`（最近一轮 rc<0 即失败）——否：单轮瞬时失败可能为环境抖动，
  连续 3 轮才构成「未推进」证据；参数可配（0=关闭）。
- 把 rc 判据下沉到设备端 loop 脚本——否：脚本资源不在本仓版本管理内，
  check 侧取证是可控面，且不改变设备端行为语义。

## Verification

- 新增 `backend/agent/tests/test_gpu_check_v107.py` 10 例：三类证据 + 防误杀
  4 例（瞬时空跑恢复 / streak 未达 / healthy / monitor protobuf）+ END 路径
  不回归 + 参数覆盖与关闭 —— 全绿；
- 反向验证：同一测试集指向 v1.0.6 → **5 failed / 5 passed**（早判类全挂，
  防误杀与 END 路径类两版同绿）——证明测试钉住新行为而非恒真；
- `pytest backend/agent/tests/ -q` 全量：**1692 passed**（约 2m41s）；
- 版本目录全量副本（ADR-0020）+ 不可变门禁与 `check:quick` 见 PR 记录。

## Revisit

若设备端 loop 改为「单轮失败即中止」语义，check 侧 rc 判据可退回 streak=1；
建议下一轮真机 GPU 压测时观察失败 run 的 `early_crash` 归因字段是否按预期出现。
