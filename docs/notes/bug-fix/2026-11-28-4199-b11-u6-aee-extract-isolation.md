# B11-U6：闭合 aee_extract 产出实证的隔离、归属与权限收口边界

Status: implemented
Class: bug-fix

检索词：B11-U6、aee_extract、隔离解包、归属绑定、chmod 000、
`safe_rmtree_no_follow`、#4199 v1.3、#4236 补充复核、#4174。

## Decision

按 [#4199](https://github.com/DUElost/stability-test-platform/issues/4199) **v1.3**
§3 B11-U6 / §4.2 与 [#4236](https://github.com/DUElost/stability-test-platform/pull/4236)
[补充复核](https://github.com/DUElost/stability-test-platform/pull/4236#issuecomment-7237069430)
复现的 3 项验收缺陷，在 U5 同一 6 文件 scope 内闭合：

1. **U4 未校验产物零泄漏**：将 `EvidenceUploader._unpack_dbg_in_place` 改为在独立
   临时目录执行单次 `aee_extract`，四重核验通过后**仅复制**已验目标 `*.DEC` 进
   `dbg.parent`；同级 `extra.DEC`、嵌套 `nested/partial.DEC`、`TimeoutExpired`
   半成品天然无法进入待发布 `temp` / 回执 `scratch`，并保留同目录先前合法 `.DEC`。
2. **单次调用归属严格绑定**：隔离 work 使 `resolve_valid_aee_extract_exp_main` 只能
   看见本次调用产出；第 2 份 `.dbg` 无产出时 fail-closed 抛 `EvidenceLayoutError`，
   不得误认第 1 份既有 `.DEC`。
3. **不可进入目录全路径 fail-closed + 清理安全边界**：
   `resolve_valid_aee_extract_exp_main` 将发现 / 核验 / 读取中的任何 `OSError`
   收口为 `EvidenceLayoutError`；新增 `safe_rmtree_no_follow`（仅 `lstat` /
   `unlink` / 对真实目录 `chmod`），供 U3 `ensure_dec_for_entry` 与 U4 临时目录清理
   使用，避免 `chmod 000` 清理 `PermissionError` 覆盖 502，且绝不跟随 symlink 改
   外部权限或删外部目标。

保持：全局补解锁上限 = 1、原件零污染、零新依赖 / 零迁移、不改 `tool_manifest.json`。

## Alternatives

- **继续原地解包 + 加强 purge**：否决。补充复核已证明超时路径绕过 purge、嵌套
  `.DEC` 不被顶层 `glob("*.DEC")` 命中；隔离解包从根上消除泄漏面。
- **仅在 `assert_no_symlinks` 包 `OSError`**：否决。`pathlib.rglob` 对 mode 000
  目录会静默返回空，权限失败发生在后续 `is_file`/`lstat`；必须在
  `resolve_valid_aee_extract_exp_main` 全路径收口。
- **把非阻塞观察 #4（既有 DEC 内容门禁 / 控制字节 / 体积上限）扩成门禁**：否决
  （#4199 §6）。

## Verification

- `scripts/run_pytest.py backend/tests/services/test_jira_handoff_b11_u3.py backend/tests/api/test_jira_handoff_api.py backend/agent/tests/test_b11_u4_evidence_dec.py backend/agent/tests/test_b8_g6_evidence_uploader.py backend/tests/api/test_b8_g6_r5_agent_receipt_recovery.py -q`
- `scripts/run_gates.py check:quick`
- `tools/dev/check-internal-ip-leak.py --check`

## Revisit

- B11-U6 独立复核通过并合入后，才可按 #4199 §5 部署含 U5+U6 的控制面 rev 并启动
  阶段 3/4 激活（生产 `current` 在此之前维持 `53d81e97`）。
- 若真实 MTK 解包在隔离临时目录与原地解包行为出现未登记差异，按 §7「判据不符」
  升级规划者，不得自行扩大 Appetite。
