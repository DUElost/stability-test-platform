# 中心存储硬链接去重（已退役）

状态：B8 G0-a 在代码中办结 `center-storage-interim-dedup`，依据是
[#3800 切盘方案 v1.2 §4 G0-a 第 6 条](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6572427631)。
本记录不表示 G0-b 已执行切盘。

旧日志在切盘后只读保留，新日志根使用 CIFS，硬链接止血不再适用。
[`center_storage_hardlink_dedup.py`](../../backend/scripts/center_storage_hardlink_dedup.py)
保留拒绝入口：所有参数均返回退出码 2，且不访问数据库或文件系统。
历史执行证据保留在 #3308。

运维人员不得再按历史告警中的链接执行硬链接去重。
Owner 必须按 ADR-0062 D12 第 2 条逐 run 确认旧日志的归档或删除。
容量告警的后续处置仍由 #3233 跟踪。
