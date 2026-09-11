# teardown 系 rc 守门（#807）——monkey_teardown / stop_aimonkey v1.0.1

Status: implemented
Class: bug-fix

## Decision

本质问题（#807，A×2，已亲验）：设备离线/命令失败时 teardown 系假全绿——

1. `monkey_teardown/v1.0.0`：`_pull_dir`/`_pull_file` 丢 rc（adb 对不存在路径 /
   offline 返回 rc=1 且不抛异常）→ 全部记 `pulled` 成功；`_kill_processes`
   ps 失败静默置空、kill 零执行后照走 force-stop；无条件
   `output_result(True)`。后果：teardown 瞬间断连（常态非异常）→ monkey 未杀
   继续跑、mobilelog 未回收、无重试。
2. `stop_aimonkey/v1.0.0`：offline 时 ps rc=1 stdout 空 → `matches=[]`；
   force-stop 不查 rc 无条件 append；remaining 查空 → `all_clear=True` →
   success——作为 teardown 第一步报「已全部停止」，实际 monkey 全家存活。

修复 = **发 v1.0.1 × 2**（对齐 monkey_check v2.0.2 的 `echo ready` 预检惯例）：

- 两端：步骤开头 `echo ready` 硬预检，rc≠0 → `output_result(False, "not reachable")`；
- monkey_teardown：pull/kill/force-stop 全链路 rc 判定；errors 汇总 →
  `output_result(False, error_message, metrics)`（附已发生清单）；
  `/sdcard/Auto` 标 `optional`（仅 offlinemonkey 睡眠模式创建，缺失跳过不计失败）；
- stop_aimonkey：`_ps_grep` 返回 `(rc, matches)`（rc≠0 不再当成"没有进程"）；
  kill/force-stop/extra 命令 rc 判定进 errors；post-kill ps 不可验证（rc≠0）→
  明确 False + `monkey_processes_remaining=-1`；`success = all_clear and not errors`。

## Alternatives

- **只加预检、不加 pull/kill rc**——放弃：预检后仍可能中途断连（pull 阶段），
  rc 盲区依旧；
- **把 `/sdcard/Auto` 从默认列表删除**——放弃：睡眠模式依赖该产物；`optional`
  标记既保拉取行为、又消除普通模式的假失败；
- **success 只看 all_clear（不看 errors）**——放弃（stop_aimonkey）：kill /
  force-stop 失败必须暴露（issue 修复方向明确"查 rc，失败 False"）；
- **原地修改 v1.0.0**——禁止（AGENTS.md 版本目录不可变硬不变量）。

## Verification

实际运行（worktree `/tmp/stp-807`，基于 `origin/main`）：

- `pytest backend/agent/tests/test_teardown_rc_guards.py -v` → **8 passed**
  （两端预检、pull 失败 / optional 跳过、ps 失败记录、kill 失败、post-ps
  不可验证、双成功路径）；
- **反向验证**：测试指向 v1.0.0 → **8 failed**（预检缺失 / rc 盲 / 假成功
  全部被抓住）；恢复后 8 passed；
- `ruff check backend/` → All checks passed；
- `check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机侧：断连窗口失败注入（teardown 时断网/拔线）——需真机环境。

## Revisit

- 若 stop_aimonkey 的 `extra_adb_commands` 在部分设备不稳定（setprop 不支持），
  可将其失败降级为独立 warning 字段、不参与 success 判定；
- pull 默认项的 optional 语义如扩展（更多条件产物），保持"按实际创建者过滤"
  的同一原则。
