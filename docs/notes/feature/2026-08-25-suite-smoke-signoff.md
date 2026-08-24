# #404 D6 真机冒烟签字 + 生产部署窗口执行记录

Status: implemented
Class: feature

## Decision

运营授权窗口内完成 #404 的最后两步：生产部署（重启激活 B/C/D/E 全部代码）+
D6 总验收真机冒烟。全程按 [验收 runbook](../../acceptance/2026-08-suite-binding-mtbf-signoff.md)
执行，矩阵 S1–S7 / R1–R4 全过，实测值已回填 runbook。

**部署**：`stability-backend.service` 重启至 main tip（`15b1d15`）；catalog 扫描
注册 `mtbf_check@1.3.0`——发现周期性扫描在 PR-E 编辑中间态时已抢先建行
（工作区即部署源的固有现象），确认 0 引用后 `force_rebaseline` 锚定最终字节。

**冒烟主线**：CLI 建套件→导入 130 条→改 1 条（times=2）→validate→导出；
Plan 10 绑定 suite；Run #224 设备 395 跑通准入链；abort 收尾触发 finish，
NFS JSON `suite_sha256` 与门禁 `exported_sha256` **逐字节相等**（R1/D6）。
S6 守卫（RUNNING 中 export 双向 409、force 不豁免）、S7 篡改→`sha_mismatch`
fail-fast→重导恢复，均实证。审计链 create/import/update/export 齐全。

## 冒烟副产品（两项记录）

1. **`push_mismatched_scripts` 不推支撑文件（真缺口，#222 暴露）**：
   轻量 sync 的 SFTP 循环只推入口文件与硬编码 `_adb.py`；agent 侧 verify
   按 manifest 校验 `_lib.py` 等支撑文件——host 缺 `_lib.py` 时 verify 正确报
   mismatch、push"成功"、reverify 仍失败 → fatal `script_verify_failed`。
   本次以整机 hot-update 解锁（正规 fallback）。修复方向：push 循环遍历
   manifest 支撑文件逐个推送——另起小 PR，不与本批混提。
2. user 构建设备被 root 前置正确拦截（#223，设计行为）；冒烟改用 P0 验收
   同款 userdebug 设备 395。

## 如何验证

- 全部证据落 runbook §2/§4 实测列：frozen sha、注入 params、NFS JSON 路径
  （`mtbf/legacy/results/2026.08.15_06.23.23.401.json`）、审计 id 序列、
  S6/S7 响应体。可独立复核。
- 生产状态：suite `MTBF-legacy`(id=1) 与 Plan 10 绑定保留为托管模式首个
  真实数据点；Run #222/#223/#225 为验收痕迹（FAILED 属预期）。

## 边界与何时重议

- sync 支撑文件修复小 PR（含单测：manifest 多支撑文件推送 + 缺失场景）；
- `suite_unbound` 观察期照旧，归零后翻转硬拒；
- P2 前端（用例管理页 + `test_case_result`）独立立项，#404 至此可关单。
