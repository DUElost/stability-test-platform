# 沉淀 P535AA V108 批量卡开机动画死锁复盘与快速排障指引（#4222）

Status: implemented
Class: process

## Decision

新增事故复盘与快速定位手册 `docs/operations/incident-2026-11-28-p535aa-v108-bootanim-deadlock-and-recovery.md`，登记至 `docs/operations/README.md` §9，并在 `.claude/skills/diagnose-device-stall/SKILL.md`「踩坑守卫（负向约束）」补充 `adb_state=device` 但 `sys.boot_completed` 为空 / `init.svc.bootanim=running` 时的首查分流规则。

## Alternatives

1. 仅保留在 GitHub Issue #4222 评论与对话历史中：不进仓内文档地图和排障 skill，后续排障人员遇到 `adb=device` 假在线卡死时仍会沿主机/USB/平台方向弯路排查，因此放弃。
2. 独立新建开机卡死 skill：当前阶段仅单次机型 ROM 缺陷触发，`diagnose-device-stall` 已有设备停滞与假失联排查入口，先在其负向约束中建立分流指针即可，不增加新的 skill 维护面。

## Verification

- 在新建文档工作树执行 `tools/dev/check-internal-ip-leak.py`、`check:quick` 治理/文档相关门禁与 `writing-conventions.md` §5 人工自查，确认无内网地址/序列号泄露与格式污染。

## Revisit

- 待 #4222 剩余两项平台侧修复（`powercycle_check` 周期性启动就绪检查、teardown 超时恢复不误重跑 `init_return_check`）合并后，在对应实现 PR 的 Agent Note 中回链本复盘即可，无需重复改写本次技术取证事实。
