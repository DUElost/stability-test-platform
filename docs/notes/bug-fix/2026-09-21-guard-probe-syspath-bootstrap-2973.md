# script_guard / pg_error_guard：sys.path 自举必须先于 tools import（#2973）

Status: implemented
Class: bug-fix

## Decision

两处探针（`script_guard_probe.py`、`pg_error_guard.py`）把
`from tools.dev.textfile_metrics …` **挪到** `sys.path.insert(REPO_ROOT)` 之后，
对齐已正确的 `skill_usage_probe.py` / `queue_head_telemetry.py`（#1659）形态，
并加 `noqa: E402`。

根因不是缺自举，而是自举写在 import **后面**——systemd `ExecStart` 以文件路径
直接调用时 `sys.path[0]=tools/dev`，顶层 `import tools` 必炸，指标生产者停摆
（控制面宿主已实测 rc=1）。

## Alternatives

- **改 unit 为 `python -m tools.dev.…`**：也能修，但要动已安装的
  `stp-script-guard.service` / `stp-pg-guard.service`；脚本侧自举与 cwd/调用形态
  无关，且与仓内其它探针一致，一次修两端。
- **只修 script_guard**：否决——issue 点名同形的 `pg_error_guard.py`。

## Verification

- `env -u PYTHONPATH python tools/dev/script_guard_probe.py --help`（仓库外 cwd）
- `env -u PYTHONPATH python tools/dev/pg_error_guard.py --help`
- `python -m pytest tests/test_guard_probe_syspath_bootstrap_2973.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

合入后控制面宿主无需改 unit——下次 timer 触发即应写出
`stp-script-guard.prom` / `stp-pg-guard.prom`；若仍 broken，查 WorkingDirectory
与部署树是否仍指向本修复前的检出。
