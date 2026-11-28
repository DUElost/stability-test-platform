# B11-U3：按冻结 manifest 受控导出 15.4 与全局单并发代表补解（#4199 v1.1 / #4174）

Status: implemented
Class: feature

关联：[#4199](https://github.com/DUElost/stability-test-platform/issues/4199)（批次 B11 方案 v1.1）、
[#4174](https://github.com/DUElost/stability-test-platform/issues/4174)（v1.1 §5.2–5.3）、
[#4054](https://github.com/DUElost/stability-test-platform/issues/4054)。

## Decision

按批次 **B11（#4199 规划方案 v1.1）** 与 **#4174 v1.1** Owner 裁决，落地阶段 3a（B11-U3）控制面 B 段受控导出：

1. **配置收敛（ADR-0042）**：新增 `backend/core/settings/jira_handoff_export.py`，登记
   `STP_JIRA_HANDOFF_EXPORT_ROOT` / `VENDOR_PATH_PREFIX` /
   `DEC_EXTRACT_MAX_CONCURRENT`（硬上限 `le=1`）/ `DEC_EXTRACT_LOCK_DIR`，并同步
   `docs/development/environment-variables.md` 与 `.env*.example`；零 Alembic 迁移。
2. **唯一冻结输入与验签**：`POST /api/v1/jira/handoff/bundles/{handoff_id}/export`
   重算 `compute_manifest_sha256` 并与冻结字段、请求期望值三方强比对；篡改即
   fail-closed。请求必须显式传入每 build 的 `target_folder_name` +
   `merge_strategy`（`create_new`|`merge_existing`），拒绝穿越/内网 IP/symlink。
3. **不覆盖防线 + 过渡期单并发补解**：目标事件目录已存在时逐文件比对非 `.DEC`
   数量与 sha256——一致幂等跳过，不一致拒覆盖；缺失 `*.DEC/__exp_main.txt` 时仅对
   选定子集在隔离临时目录补解，跨进程 `flock` 全局并发 = 1（`ionice -c 3`）。
4. **发布硬门槛**：逐代表核验非 `.DEC` 完整性、可读 `__exp_main.txt`、厂商 Path
   映射回本机导出根 `isfile` 且非 symlink；通过后先 `.bak-<ts>` 备份既有终稿再原子
   写入 `Result_None_None_MonkeyAEE_SH_<ts>.xls`（15 列 + `"ExpType "` 尾空格 + B3
   公式中和）。原冻结 `manifest.json` 不可变；审计写入 `export_receipt.json` 并投影
   `EXPORTED`，`record_audit(jira_handoff_bundle_exported)` business 90d。
5. **Excel 依赖不扩散（#739 / 第 1 轮复核）**：B 段终稿渲染复用 U1 已登记的
   `_render_preview_form_xls`，`jira_handoff_export.py` 不直接 `import xlwt`，避免
   触发 `test_excel_dependency_inventory` 台账扩散。

## Alternatives

- **方案甲（就地改写 `manifest.json` 的 `status` 为 `EXPORTED`）**：否决。会破坏
  冻结 `manifest_sha256` 验签契约；改为并列 `export_receipt.json` 投影状态。
- **方案乙（单任务内 `max_workers=1` 但不设跨请求全局锁）**：否决。违反 #4174
  §5.3 与 #4199 过渡期补解全局总量边界（控制面多导出任务并发仍会拖垮磁盘）。
- **方案丙（B 段自动推断目标目录日期）**：否决。与 SOP 阶段 G/H 及 #4199 人工
  显式确认不变量冲突。

## Verification

见 PR 正文「验证结果」节（#4199 v1.1 §4.2 B11-U3 全部 5 类反例、U1 回归、
`check:quick`、`check-internal-ip-leak.py --check`）。

## Revisit

- Owner 指定 15.4 独立测试目录做 L2 受控导出验收，实测耗时与控制面 I/O 回填 #4054；
- B11-U4 稳态代表 `.DEC` 保全落地后，过渡期控制面补解路径应收敛为罕见兜底；
- 若生产补解锁目录需落在持久盘而非 `/tmp`，仅调 `STP_JIRA_HANDOFF_DEC_EXTRACT_LOCK_DIR`。
