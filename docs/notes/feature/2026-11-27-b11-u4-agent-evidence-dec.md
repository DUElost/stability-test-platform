# B11-U4：Agent 稳态代表证据上传保全并校验 `.DEC/__exp_main.txt`

Status: implemented
Class: feature

检索词：B11-U4、EvidenceUploader、`.DEC`、`__exp_main.txt`、原件零污染、
`dir_sha256_and_size`、ADR-0062 I4、#4199、#4174。

## Decision

按 #4199 v1.1 §3 B11-U4 / #4174 §5.3，采用**方案 A**：`EvidenceUploader._copy_to_destination`
先把本地原件 `src` 无损复制到隔离临时目录 `temp`，再仅在 `temp` 内并入或按需解包
生成同一次发生的 `*.DEC/__exp_main.txt`，随后把含 `.DEC` 的目录纳入
`assert_no_symlinks` / `dir_sha256_and_size(temp)` 校验与原子 `os.rename(temp, dest)`
发布回执。上传前后对 `src` 再算 `dir_sha256_and_size`，必须与复制前一致（原件零污染）。

解包定位扫描工具同树的 `tools/aee_extract/aee_extract`（`STP_DEDUP_SCAN_SCRIPT` 或
已核验的 packaged scan tool root）；工具缺失、无 `.dbg`、或解包失败时打可观测告警并
降级为不含 `.DEC` 的发布（由控制面 B11-U3 过渡期补解兜底），不崩溃、不改写、不 prune
本地原件（ADR-0062 D8 第 4 条 / I4）。不修改 `EventUploader`、`scan_runner.py` 回收路径、
`backend/agent/scripts/` 或 `tool_manifest.json`。

预算耗尽恢复（`_recover_exhausted`）改为在同父目录隔离 scratch 上复算与正式发布相同的
身份（含可选 `.DEC`）再与既有 `dest` 比对，仍禁止向正式 destination 叶子写入。

## Alternatives

- **方案 B（scan 回收前侧边缓存）**：在 `reclaim_scan_staging()` 前把 staging 内已解析
  `.DEC` 按发生身份落到独立缓存，再供 uploader 并入。能复用扫描已解包产物、避免代表
  上传时再跑 `aee_extract`，但要新增缓存生命周期/容量约束，并改动 `scan_runner.py`。
  方案 A 已满足原件零污染与校验闭合，本单元不选 B。
- **在原事件目录就地解包**：最短路径，但直接违反原件零污染硬红线；放弃。
- **EvidenceUploader 提前 prune 本地源**：与 ADR-0062 I4 / D8 第 4 条冲突；放弃。

## Verification

- `scripts/run_pytest.py backend/agent/tests/test_b11_u4_evidence_dec.py backend/agent/tests/test_b8_g6_evidence_uploader.py -q` → **28 passed**
- `scripts/run_pytest.py backend/agent/tests/ -q` → **3731 passed, 2 skipped**；
  `test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails` 失败（本机进程
  无 `dialout` 组、与本 diff 无关，单测复现同失败）
- `scripts/run_gates.py check:quick` → **[OK] check:quick (16 gates)**
- `tools/dev/check-internal-ip-leak.py --check` → **通过**（4415 个文件）

## Revisit

- 试点主机热更新后观察：代表 `dest` 是否稳定含 `*.DEC/__exp_main.txt`；`aee_extract`
  不可用时的降级告警频率；旧 Agent 已发布无 DEC 的 `dest` 与新 Agent 含 DEC 重试是否
  触发 `destination_conflict`（预期 fail-closed，由 U3 补解或重建发布处理）。
- 若代表上传阶段 `aee_extract` I/O 过重，再评估切方案 B（扫描侧边缓存）或与 U3 补解
  配额统筹。
