# 展锐 NR 平台 NV 条目未受保护：P532AA 整包刷写会覆写 IMEI/校准（#716）

Status: implemented
Class: bug-fix

载体 issue：#716（Epic 展锐全链路闭环）；NV 策略裁决在 #3758 决策表第 2 行
（「只不写」：persist / prodnv / NV 保留）。

## Decision

`tools/dev/prepare_unisoc_firmware.py` 的 `PRESERVE_IDS` 补三个 NR 条目：
`NV_NR`、`Modem_NR_DELTANV`、`NV_CALI_NR`。`build_plan` 的判定形态不变，
仍是按 PAC 文件 ID 白名单打 `preserve`，`flash_firmware_unisoc` 侧
`flash_partitions` 已经跳过 `preserve` 条目，因此只改这一处。

根因：`PRESERVE_IDS` 是随 ums9230（A57 / Qogir L6，LTE-only）首版写下的，
名单里只有 LTE 侧的 NV ID。Qogir N5（ums9632，P532AA）把同一角色的分区换了
名字，白名单没有跟着换：

| P532AA PAC 文件 ID | 目标分区 | A57 同角色 ID（已在名单） | 修复前 |
|---|---|---|---|
| `NV_NR` | `nr_downloadnv_a` | `NV_LTE` → `l_fixnv1_a` | 被刷 |
| `Modem_NR_DELTANV` | `nr_deltanv_a` | `Modem_LTE_DELTANV` → `l_deltanv_a` | 被刷 |
| `NV_CALI_NR` | `calinv` | `NV_CALI_LTE` | 该 PAC 无实体文件，未受影响 |

`ProdNV`/`Persist`/`UserData` 在新 PAC 里沿用同名 ID，仍然命中名单，所以缺陷
只表现为「NR 的三个 ID 漏了」，而不是整体策略失效。

不能改成按 scheme `Type` 匹配：实测同族 `ProdNV`、`Modem_NR_DELTANV`、
`NV_CALI_NR` 的 `Type` 都是 `CODE2`，与普通代码分区同值，只有 `NV_NR` 是
`NV_COMM`。按类型过滤既会漏掉 deltanv/calinv，也会误保留代码分区。

## Alternatives

- **产物侧手工给 `plan.json` 打 `preserve`**：本次刷机可用，但下一次重跑
  `prepare` 会静默回到不安全状态，且没有任何检查会发现。属于未标注的过渡止血，
  不采纳。
- **不生成这两个镜像文件**：`build_plan` 对磁盘上不存在的条目直接跳过，效果
  等价于 preserve。但产物目录从此与 PAC 不一致，事后无法核对「这次到底刷了哪些
  分区」，可审计性更差。
- **在 `flash_firmware_unisoc` 加分区名黑名单**：把策略从产物挪进脚本，与
  「脚本只做解析→刷写→擦除→回读核验、保护名单住在 plan」的既有分工冲突，
  且要为每台新芯片再改一次脚本。

## Verification

- `scripts/run_pytest.py tests/test_prepare_unisoc_firmware.py -q` → 3 passed；
  `test_build_plan_marks_device_unique_entries_preserve` 扩展到 LTE + NR 五个
  ID，并断言 `BOOT` 仍不带 `preserve`。
- 产物侧（本 Note 合入前用本分支工具生成）：对 P532AA EVT PAC 跑 `prepare` 后
  `images/plan.json` 中 `NV_NR`/`Modem_NR_DELTANV` 必须为 `preserve: true`，
  `flash_firmware_unisoc --dry-run` 的分区清单必须不含 `nr_downloadnv_a`、
  `nr_deltanv_a`、`calinv`、`splloader`。
- 真机（主机 .82 / `MSW000003J6AU****`，eMMC）：刷后 `adb root` 可用后读
  IMEI 非空、`ro.build.type=userdebug`。刷机前是 user/release-keys build，
  `adb root` 被拒，**没有刷前 NV 校验和基线**，只能靠刷后 IMEI 与射频可用性判定。

## Revisit

- 本 Note 只补了 P532AA 一个 NR PAC 观察到的 ID。厂商若改 ID 命名（例如再出
  `NV_NR_2` 或 WCN NV），白名单会再次静默失配——正解是产物生成期对「目标分区名
  含 nv/cali/delta 但未标 preserve」的条目直接报错，而不是继续加名字。此项未立单。
- `prepare_unisoc_firmware.py` 把 `version_prop` 硬编码为
  `ro.build.sw_internal_version`（A57/中兴侧属性）。P532AA 上该属性为空，
  `flash_firmware_unisoc` 的 `verify_version=true` 会把成功刷写判成失败，本次只能
  以 `verify_version=false` 绕过并手工核验。属于 #3779（刷机族厂商变体收口）
  的同一形态，不在此单扩大范围。
- `_MODEL_FAMILY_ROUTES` 尚无 p532a 条目，本次靠显式 `family`+`version` 参数路由；
  若 P532AA 要放量，需要把机型路由与 `flash_preflight_unisoc` 的 identity 白名单
  一并参数化或登记，否则每台都要手工传参。
