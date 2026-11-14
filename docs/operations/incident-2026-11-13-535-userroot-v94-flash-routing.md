# 2026-11-13 535 全队 userroot V94 刷机战役复盘（fingerprint 路由三元耦合与显式路由切换）

> **状态**：v3 批次（plan 186/187，显式 `firmware_dir` 路由）已收口；93/134 台 COMPLETED，41 台 FAILED 按签名分类处置（10 台重派 run 1347/1348、24 台滞留 BROM 待现场重插、7 台已刷成 V94 但后置链失败）。无包缺陷、无镜像损坏。
> **影响范围**：run 1264（535 Monkey，135 台 ABORTED）腾出的 135 台空闲设备；NX2 71 / NX3 54 / NX3Carrier 10。两次整批秒败（1342/1343）**无烧写副作用**，但消耗一轮派发与全部设备的欠账放行。
> **关联**：[runbook §1](../operations/honor-flash-runbook.md)（userroot 包 incremental 陷阱）、[设计文档 §2](../design/2026-08-honor-flash-firmware-routing.md)（路由模式选型约束）、#3895（abort 欠 oobe 欠账）、#3931（认领排队超窗）、#3867（UNKNOWN grace 家族）。

---

## 1. 现象与时间线

| 批次 | plan / run | 参数形态 | 结果 |
|------|-----------|----------|------|
| v1 canary | 181/182/183 → 1336–1338 | `family=MLD` + `version=包口径` | 三台卡 PRECHECK：run 1264 abort 留下 135 台 gpu/monkey/power-cycle `executed_unverified` 欠账，准入被锁 |
| v1 canary 重派 | 1339/1340/1341 | 同上 + D5.7 return-override 放行 canary 3 台 | 1341（Carrier）COMPLETED；1339/1340 FAILED：`post-flash version mismatch: expected P535AA-HONOR-NX{2,3}-16-261110V94, got P535AA-HONOR-NX{2,3}-16-user-261110V94`——**烧写成功**，verify 用错串 |
| v2 批量 | 184/185 → 1342/1343 | 修 manifest.version 加 `user-` 段，params.version 同步改，路由仍为 fingerprint | **125 台全部 FAILED**：`firmware_dir not found: /mnt/stp-aee/firmware/MLD/P535AA-HONOR-NX{2,3}-16-user-261110V94`（目录名沿用包口径，不含 `user-`）；Carrier 1344（plan 183 显式路由）不受影响正常推进 |
| v3 批量 | 186/187 → 1345/1346 | 显式 `firmware_dir`（目录名/params/manifest 三者解耦） | 88/125 成功（NX2 51 + NX3 37），Carrier 1344 5/9；失败按 §4 分类，无路由类错误 |

三包（`P535AA_honor_NX{2,3,NX3Carrier}_16_userroot_261110V94_FLASH.zip`）验包全绿：59 文件逐文件 size+CRC 一致、testzip 全绿、三处版本串 `261110V94`；SKU 取证 NX2=MLD-NX2、NX3=MLD-NX3、Carrier=MLD-NX3-Claro（与现网一致）；Carrier fingerprint 由 vbmeta 实锤（`HONOR/MLD-NX3-Claro/HNMLD-M1:16/BP2A.250605.031.A3/261110V94:user/release-keys`）。

## 2. 根因判定

结论：**userroot 构建变体的 `ro.build.version.incremental` 带 `user-` 段，与「目录名 == params.version == manifest.version」三元一致的 fingerprint 路由不兼容**；且局部修正（只改 manifest.version）会把 canary 单台的 verify mismatch 放大成整批 `firmware_dir not found`。

- 包口径（目录名、boot.img 内嵌 fingerprint）：`P535AA-HONOR-NX2-16-261110V94`；
- 设备真值（`ro.build.version.incremental`，`post_flash_verify.current`）：`P535AA-HONOR-NX2-16-user-261110V94`；
- 两者不等是 userroot 包的常态（boot fingerprint 不含该段），验包若只取 boot fingerprint 必然漏判；
- fingerprint 路由（`_resolve_by_firmware`→`_resolve_by_fingerprint`）用 `join(root, family, version)` 拼目录并校验 `manifest_version == version`：params.version 取包口径则 verify mismatch（1339/1340），取 incremental 口径则目录不存在（1342/1343）；
- 显式 `firmware_dir` 路由（`decided_by=params`）免目录名一致校验，刷后比对按 manifest.version 执行——Carrier plan 183 从一开始就走该模式，故同批包中唯一未受两次失误影响。

## 3. 修复与实证

v3 形态（plan 186/187 的 flash 步骤参数，与 plan 183 同模式）：

```json
{"firmware_dir": "/mnt/stp-aee/firmware/MLD/P535AA-HONOR-NX2-16-261110V94",
 "verify_version": true, "skip_if_current": true, "verify_wait_seconds": 900}
```

- 目录名沿用包口径（不动已上架发布单元）；manifest.version 用真机 `getprop` 回读值（`user-` 形）；plan 不传 `family`/`version`；
- 实证：1345/1346 零 `firmware_dir not found`、零 verify mismatch；Carrier 1344 全程同模式正常；
- 已刷成 V94 的 canary（928/1021/1029）在批量里由 `skip_if_current` 自动跳过 flash，只补 oobe/root/check。

放量前的检查口径（补充 runbook §4 验证点）：flash 步骤 `metrics.route.decided_by == "params"`、`metrics.firmware_dir` 指向实存目录、`metrics.post_flash_verify.current == manifest.version`；三者任一不符即停止放量。

## 4. 失败签名分类（v3 收口 41 台）

| 签名 | 台数 | 定性 | 处置 |
|------|------|------|------|
| `no pass token found` / `fail token hit: FAIL` | 24 | A 类 BROM/SLA 认证瞬态（历史 ~10–14%，1001/1033 先例重试即愈），设备滞留 preloader | 现场重插后重派；平台侧无救砖通道 |
| `'PipelineEngine' object has no attribute '_last_step_error'` | 8 | **新 agent 缺陷**：`device_return.py:56` 引用 PipelineEngine 不存在/未初始化的属性；与路由无关 | 6 台已纳入重派 1347/1348；复发必须查 host 上 agent revision 并开单 |
| `not adb-ready within 600s; OOBE skip not attempted` | 4 | 刷后 oobe 等待窗内设备未回 adb（同 944/957 先例，深度亏电/首启慢）；设备版本已是 V94 | 重插充电后补 oobe_skip，无需重刷 |
| `init_check_unreported` / `device_lease_not_held` | 5 | 调度/lifecycle 竞态（欠账 override 后的 init_check 上报、租约竞争） | 重派即愈（10 台重派批次） |

另有 7 台 build_display_id 已含 `261110V94`（烧写成功）——**判定「已达标」按设备当前版本，不得按 job 终态**；job FAILED 只说明后置链或核验失败。

## 5. 教训与动作

1. 上架 userroot 包时，运维必须同时提取 boot fingerprint 与真机 incremental；两者不等时不得使用 fingerprint 路由（已落 runbook §1 与设计文档 §2）。
2. canary 暴露 `post_flash_verify` mismatch 后，运维不得只改 manifest.version 就放量；必须先确认路由模式，incremental 与目录名解耦的包一律切显式 `firmware_dir`，并复核 `metrics.route.decided_by`（已落 runbook §4）。
3. 批量重派前必须清点并放行 abort 遗留欠账（D5.7 `return-override`，`evidence_ref` 必填）：1336–1338 卡 PRECHECK 即因此；135 台放行后准入零阻塞。
4. 派发量必须按 #3931 分批（~17 台/批），不得一次 135 台撞认领排队超窗与 batch barrier 等齐失败。
5. `PipelineEngine._last_step_error` 缺陷待开单跟踪；24 台 BROM 滞留设备待现场重插后重派（plan 186/187/183 可复用，skip_if_current 自动跳过已达标设备）。

---

**关联实证载体**：run 1339–1341（canary）、1342/1343（v2 全败）、1344（Carrier 对照）、1345/1346（v3）、1347/1348（重派）；plan 181–187；包路径 `/data/apk-repo/incoming/firmware/` 与上架目录 `MLD/P535AA-HONOR-NX{2,3}-16-261110V94`、`NX3Carrier/261110V94-userroot`。
