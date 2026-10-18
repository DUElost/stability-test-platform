# B6-U5：开关机接入 powercycle_disarm 1.0.3 与 powercycle_finish 1.1.0

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) **v1.7** §3.0.2 / §3.6 / §3.6.1–§3.6.5 / §4 U5
与 ADR-0061 Accepted v1.1（含 D2.5）实施 [#3730](https://github.com/DUElost/stability-test-platform/issues/3730)。
方案 A 旧包处置见 [#3751](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234224273)。
Draft PR 待 Planner 确认与独立复核；合入不等于生效（发包/scan/热更新/重指受 §5 整批闸门）。

## Decision

1. 新族 `powercycle_disarm`：**1.0.3**（承接 1.0.2 的 §3.6.1.3；追加 §3.6.2 等待上线 15s
   PROGRESS 轮询）。参数 `mode`（`probe`/`disarm`）、`wait_device_online_seconds`。
2. job 级步骤角色 helper（§3.0.2）：`powercycle_finish@1.0.x` 仍解除；`@1.1.0` 只收取；
   未知/缺版本保守保留解除义务（`unknown_legacy_version`）；收取失败不得覆盖已完成 disarm。
3. Plan 保存入口 §3.6.5：三入口 422 `ONBOARDED_TEARDOWN_INVALID` + 四种 reason。
4. 旧条目 1.0.0/1.0.1/1.0.2 → `retired: true`（不删、不改 SHA）；
   `check_script_packages.py --publish-restored`（方案 A，仅临时站点目录验证）。
5. `ONBOARDED.disarm_min_version=1.0.3`；模板 pin
   `[powercycle_disarm 1.0.3 @660s, powercycle_finish 1.1.0 @420s]`；
   `verified_device_classes=[]`（真机 pending）。
6. `powercycle_finish@1.1.0` **保持原包**。不改 U8 reaper/guard/类别政策；不 Ready/merge/publish/scan。

## Alternatives

- 把全部 finish 重新纳入解除或删 ONBOARDED：违反 §3.6.2，未采用。
- 方案 B（不发布旧包、改 SOP）：Owner 未选，未采用。
- 虚填已验证类别：违反 D2.5，未采用。

## Verification

- 门禁：`check_script_packages`（含 `--self-test` publish-restored 五项）；
  `check_tool_manifest --base origin/main`；`check:quick`。
- 临时站点目录对 1.0.0/1.0.1/1.0.2 真实 `--publish-restored`：写入 / 同字节跳过 / 异字节拒绝。
- 树对象可达并 SHA 与 manifest 一致。
- §3.6.5 / §4.1 真入口三入口反例与正向对照；Engine→runner 角色对照；15s 轮询进度。
- 真机：MLD_LX2/LX3/NX2 **仍 pending**（`adb devices` 空）；空验证表。

## Revisit

- 真机资源到位后按 D2.5 补三类验证并追加版本登记。
- U3 合入后基于最新 main 再集成并重跑入口验收。
- 激活按 #3723 §5；不得因本单元单独发包/scan/热更新/重指。
