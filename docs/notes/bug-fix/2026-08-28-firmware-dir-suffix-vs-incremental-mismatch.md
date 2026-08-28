# 固件包目录名与 ro.build.version.incremental 后缀不一致——verify 误判

Status: implemented（2026-08-28 现场修复）
Class: bug-fix

## Decision

MLD-LX2 固件包目录名 `V552AA-HONOR-LX2-16-260810V62_FTM_userdebug` 带
`_FTM_userdebug` 后缀，但设备刷完后 `ro.build.version.incremental` 返回
**不含后缀**的 `V552AA-HONOR-LX2-16-260810V62` → manifest/latest 与设备
比对恒 mismatch → post-flash verify 误判失败（Run #248 共 7 台误判）。

修复：目录名、manifest.version、latest.json **三处一致化**为设备实际
返回值（去后缀）；已刷上的设备重派走 SKIPPED 快路径收尾。

## Alternatives

- 脚本端对 version 做后缀归一：掩盖差异而非消除——固件组命名习惯未知，
  归一规则不可靠。
- 保留后缀并接受 verify 误判：失败率不可接受。

## Verification

- 三处一致化后：Run #248 后段 verify 通过（COMPLETED 正常增长）；
  重派 6 台（Run #250）6/6 SUCCESS，5 台设备实测
  `ro.build.version.incremental` == manifest version。
- LX2 20 台批量验证最终 19/19 达成目标版本（1 台真实离线 6482 待人工）。

## Revisit

- **固件上架 SOP 新增检查项**：`manifest.version` 必须以
  `adb shell getprop ro.build.version.incremental` 实测值为准，
  目录名带 `_FTM_` 等构建后缀时**先在一台真机确认实际返回串**再写 manifest。
- 根治方向（已建议）：族级 latest.json 升级为 per-model versions 映射，
  使多机型多版本并存路由（见 PR 讨论 / Agent Note 2026-08-28-flash-plan-version-mechanism）。
