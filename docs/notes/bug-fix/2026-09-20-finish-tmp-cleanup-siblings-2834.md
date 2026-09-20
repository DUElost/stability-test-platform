# powercycle_finish / sleep_finish 回收自建临时结果目录（#2834 同形收口）

Status: implemented
Class: bug-fix

## Decision

#2834 根修只发了 `gpu_finish` v1.0.6（GB 级、已致 ENOSPC）。同 issue 评注指出
`powercycle_finish` / `sleep_finish` 是同一 `mkdtemp` 形状，当时因体积小未各开
全量副本。本 PR 按同一形状补齐：

| 族 | 新版本 | 前缀 |
|---|---|---|
| `powercycle_finish` | v1.0.5 | `powercycle-results-` |
| `sleep_finish` | v1.0.3 | `sleep-results-` |

行为与 gpu_finish v1.0.6 对齐：`_mk_result_tmpdir` 建即登记；`main()` 的
`finally` 回收（含 `SystemExit`）；形状不符不删。

未做：存量 `/tmp` 清理、fleet 观测、模板/plan_step 重指、`POST /scripts/scan`
（均属运维或独立缺口，与 #2834/#2931 边界一致）。

## Alternatives

- **继续等各自下次 bump 顺带收**：泄漏面仍开；#2834 已点名同形，再拖只会多积一截。
- **抽公共 helper 包三族**：ADR-0029 全量副本下 helper 仍要拷进每版目录，收益小。

## Verification

- `pytest backend/agent/tests/test_finish_tmp_cleanup_siblings_2834.py -q`
- `pytest tests/test_finish_tmp_cleanup_siblings_guard_2834.py -q`
- `tools/dev/check-script-version-immutability.py --base origin/main`

## Revisit

- scan 注册 + 引用重指后，本收口才对线上生效（见 #2931 盲区）。
- 若再发现第三处裸 `mkdtemp(prefix=*-results-)` 无回收，扩守卫名单。
