# scan staging 回收周期：每轮扫描结束即清理（#1078）

Status: implemented
Class: bug-fix

## Decision

#1078（R10-F10）：MTK 扫描的 staging 目录（`.stp-scan/pr{plan_run_id}-<rand>/`，
内含 HDD 事件文件的**硬链接**）只在同一 PlanRun 下次 prepare 时清旧目录；PlanRun
终态后无回收路径。后果：删除原事件目录后，空间因 `.stp-scan` 里的硬链接引用仍
被占用而无法释放，HDD spill 被架空。

修复：把回收周期从「下一轮 prepare / 终态钩子」改为「**每轮扫描结束**」——

- 新增 `reclaim_scan_staging(scan_root)`：只删 `.stp-scan/` 下 `pr<plan_run_id>-*`
  目录，best-effort 永不 raise。**硬闸**：parent 不是 `.stp-scan`、或名字不像
  staging（serials 为空时 `scan_root` 就是 HDD 根）一律拒绝动手，防误删不靠调用方
  自觉；
- `run_local_scan`：失败路径（工具非零退出 / 无新鲜 org.xls / 超时 / 异常）在
  finally 里立即回收 —— 失败轮不产生可上传产物，staging 纯属垃圾；成功路径把
  staging 根记到 `self._last_scan_root`，**不能删**（org.xls 还在里面等上传）；
- `run_scan_and_upload`：产物复制进 dedup/ 目录后（含 uploader 未配置的早退分支）
  回收 staging —— 至此本轮生命周期闭环，不再遗留任何等待「终态钩子」的目录；
- `_prepare_scan_root` 原有的「同 PlanRun 下次 prepare 清旧」逻辑保留不变（双保险，
  兜进程中途被 kill -9 的残留）。

回收周期总结：**成功 = 上传完成后回收；失败 = 立即回收；进程死亡 = 下一轮 prepare
清扫**。终态后无需专门回收 —— 正常路径下终态时 staging 早已不存在。

## Alternatives

- PlanRun 终态钩子清理：需要 Agent 感知控制面的终态事件（新增跨进程通知），且
  窗口内垃圾仍在占空间 —— 收益全面劣于逐轮回收；
- 上传成功后只删产物文件、保留目录结构：残目录会无限累积（每轮一个
  `pr*-<rand>`），且空目录无诊断价值；
- 把 org.xls 复制出 staging 再即时清目录：多一次全量拷贝（org.xls 可能不小），
  仅为了省一次延迟回收，不值。

## Verification

- `pytest backend/agent/tests/test_scan_runner.py`：30 passed，新增 6 例 ——
  HDD 根拒绝回收 / 非 `.stp-scan` 父目录拒绝回收 / 删 staging 后原事件文件
  `st_nlink` 从 2 回落到 1（prune 后空间可释放的直接证据）/ 失败路径立即回收 /
  成功路径保留 staging 等上传 / `run_scan_and_upload` 端到端上传后回收且
  `st_nlink` 归 1；
- `pytest backend/agent/tests`：全目录通过；ruff 干净。

## Revisit

- UNISOC 链路（`unisoc_scan_runner.py`）无 staging 硬链接结构，本单不涉及；若
  将来 UNISOC 也引入 scoped staging，直接复用 `reclaim_scan_staging`；
- 进程被 kill -9 时本轮 staging 仍靠下一轮 prepare 清扫兜底 —— 若出现「Agent 单
  轮生命周期」（不再有下一轮），需要启动时对 `.stp-scan` 做孤儿清扫，另开单；
- 回收是 `shutil.rmtree` 同步执行，staging 大时（万级文件）会占住扫描线程数百毫秒
  —— 实测如成瓶颈，可挪到独立清理线程。
