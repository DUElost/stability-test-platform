# pr-agent-tests：AST 共享解析与超时用例去墙钟

Status: implemented
Class: testing

## Decision

在 #3641（`await_health` stub）之后，继续砍 `pr-agent-tests` 墙钟，两条并行手段：

1. **共享解析 / 内容寻址缓存**（不断言、不改 baseline）：
   - `tools/dev/check_resource_anchors.py`：`_analyze_file` 按
     `(rel, sha256(bytes), sibling_stems)` 缓存 `FileAnalysis`。同一次
     `analyze()` 内的重复 parse、以及隔离副本间未改动的文件均命中；变异改内容或
     邻居 `*.py` 集合则 miss，棘轮语义不变。
   - `tests/test_check_resource_anchors.py`：session 级 pristine subset 模板，
     IsolationMutations 从模板 `copytree`，不再每例从 live tree 重铺。
   - `tests/test_source_scan_anchor_ratchet.py`：默认 `SCAN_DIRS` 语料 parse 一次；
     offenders / callee keys / axis2 位点差共享；`tmp_path` 判别用例仍现场 parse。
   - `tests/test_agent_clock_stub_guard_3202.py`：`current_debt()` `lru_cache`；
     锚点探测改为 short-circuit 扫到首个 sleep stub。

2. **超时标记 / fail-fast 用例去掉生产墙钟预算**（不改生产默认超时）：
   - flash `test_adb_unreachable_fail_fast`：`model_ready_wait_seconds=0`
   - aee_prepare：`fake_shell` 对 `get-state` 立即返回 `device`
   - gpu retry：`STP_GPU_INSTALL_RETRY_BACKOFF_SECONDS=0`
   - priv `read-kernel-log` timeout：`exec sleep 60` + 既有 0.3s 预算（kill 后立即收尸）

## Alternatives

- 只缩短生产超时：否决，会削弱真机等待语义。
- 把 AST 缓存做成跨文件共享 helper：收益有限，本轮保持局部、对齐
  `test_api_response_shape_contract` 的 `lru_cache` 先例。
- 等 #3642（G2 有限值证明 draft）合入后再动 checker：会推迟 ~30s 级 census 收益；
  本轮对 `_analyze_file` 的改动面小，与 #3642 追加的有限值分析可机械合并。

## Verification

```bash
./scripts/project_python.sh scripts/run_pytest.py \
  tests/test_check_resource_anchors.py \
  tests/test_source_scan_anchor_ratchet.py \
  tests/test_agent_clock_stub_guard_3202.py \
  tests/test_agent_priv_read_kernel_log.py::test_timeout_is_rejected_with_marker \
  -q --durations=40

./scripts/project_python.sh scripts/run_pytest.py \
  backend/agent/tests/test_flash_firmware_v120.py::TestFingerprintRouting::test_adb_unreachable_fail_fast \
  backend/agent/tests/test_aee_prepare_v100.py \
  backend/agent/tests/test_gpu_power_sleep_resources.py::test_gpu_setup_retry_uninstall_only_failed_apk \
  -q --durations=20
```

本机实测（cgroup 6G / swap=0；CPU 慢于 GHA，墙钟 sleep 与 CI 同量级）：

| 套件 | before | after |
|---|---:|---:|
| 上述 offline 四文件合计 | 94.83s | 42.63s |
| `test_check_resource_anchors` call 和 | 62.38s | 29.55s |
| `test_source_scan_anchor_ratchet` call 和 | 23.60s | 11.59s |
| `test_agent_clock_stub_guard_3202` call 和 | 3.67s | ~0.9s（余下 <5ms） |
| kernel-log timeout 单例 | 5.00s | 0.30s |
| agent 三超时热点（6 例） | 160.17s | 0.12s |

## Revisit

- 与开放 draft #3642（同改 `check_resource_anchors`）合并时优先保留内容寻址缓存键。
- 若 IsolationMutations 再增长，可考虑把「绿基线 analyze 一次」做成 session fixture，
  仅对变异文件失效（需证明与当前逐例 analyze 等价）。
