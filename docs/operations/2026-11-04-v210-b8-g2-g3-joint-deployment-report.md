# Start-Log-Scan v2.1.0 与 B8 G2/G3 联合部署报告（2026-11-04）

> 状态：**执行中止并已回退**。本报告记录一次按计划执行到金丝雀即触发安全停止的部署；当前生产仍在 BASE 版本，工具仍为旧版，影子开关关闭。
> 对应计划：`docs/operations/2026-11-04-v210-b8-g2-g3-joint-deployment-plan.md`（v1.0）。
> 执行时间：2026-11-04 18:00–18:33（Asia/Shanghai）。

## 1. 结论

| 项目 | 状态 |
|---|---|
| 代码部署（G2/G3） | **已执行，已回退**；生产 `current` 回到 `eeab11d8-20261103` |
| 工具换版 v2.1.0 | **已落位并切换，已回退**；新目录保留，生产 env 指回旧工具 |
| G2/G3 现行兼容验收 | **未通过**：金丝雀扫描在 G3 映射校验处失败 |
| 影子激活 | **未开始**；开关关闭 |
| 完整 B8 阶段一对拍 | **未开始** |
| 机队滚动更新 | **未执行**；仅金丝雀 15.101 更新后回退 |

安全停止触发点：金丝雀 Agent 的 `report_mapping` 发现报表第 2 行携带本 run 设备集之外的串号，按 G3 契约拒绝整轮扫描；该轮未上传任何产物。按计划 §4.2 第 5 条执行 §7 回退。

## 2. 基线与目标

- BASE：`eeab11d8aa79a620955ecff3cb6ee0bb3912d9aa`；生产 `current` = `/home/debian13/stp-releases/eeab11d8-20261103`。
- 窗口开始时 `origin/main`：`e48e6a43cb046d53659acc2b7f2bb3611e5d4852`。相比计划写的 `6b0590a` 只多出 #3878（ADR-0062 v1.1 文档）与依赖更新；G2/G3 代码相同，无新增迁移、无脚本族/`tool_manifest.json` 变更、前端仅依赖文件变化。
- 本次冻结 TARGET：`e48e6a43`。
- 目标 bundle：`/home/debian13/stp-releases/e48e6a43-20261104`
  - agent-code `sha256:bd3da535aa03ff06e7e77f57a35c582360f5454a9f153e585b46b81687ff4500`
  - control-plane `sha256:9ab73f917d5fa29448709d826ab97018d85ca93fcd5c51dc5e5faf7bb6e09901`
  - schema_target `b8g1c4a7d2e9`
- 工具 tag：`start-log-scan/v2.1.0` → commit `9a1fdab479b68519edb7ad1728704a2b54f05893`；tag 对象 SHA 为 `7a2c4c18…`，不作为源码提交。
- 新工具目录（已保留）：
  - 控制面：`/home/debian13/start-log-scan-releases/2.1.0`
  - Agent：`/mnt/stp-aee/tools/Start-Log-Scan-releases/2.1.0`

## 3. 窗口前门禁与离线验证

### 3.1 只读门禁

- 生产 `/health` 200，schema `b8g1c4a7d2e9` = head；`current` 指向 `eeab11d8-20261103`。
- `BASE..TARGET` 内批次 issue 仅 B8（G2/G3）；无其他批次 issue；无新增迁移；无脚本族/manifest 变化；无 `DO-NOT-SWITCH.txt`。
- `check-deploy-source.sh`：OK；`check-deploy-readiness.py --diff-base eeab11d8…`：OK（含 alembic、MTBF、env 键检查）。
- fleet：77 台未退役、全部 ONLINE；预检时 65 台空闲、12 台有活跃作业；全部 `agent_code_sync_status=matched`、revision `eeab11d8`。
- 逐台工具探测：77/77 无 `STP_DEDUP_SCAN_PACKAGE_REF`；Agent `.env` 均指向 `/mnt/stp-aee/tools/Start-Log-Scan`；`/mnt/stp-log` 均挂载，控制面可用 2.5 TB。
- **阻断发现 1**：17 台 Ubuntu 22.04 主机（Python 3.10）的共享工具 venv 只有 `lib/python3.13/site-packages`，无法导入 xlrd；其余 60 台 Python 3.13 正常。
- **阻断发现 2**：新导出 `config/pkglist.txt` 为 406 行，生产为 5149 行；直接用新配置会让同一批 Run 1193 报表的 merge 结果从 14 个问题（仅 MLD-NX2）变为 48 个（MLD-LX3 34 + MLD-NX2 14）。按 Owner 裁决采用双版本 venv + 生产 pkglist 覆盖。

### 3.2 工具导出与离线验证

- `release/make_release.py --tag start-log-scan/v2.1.0` 成功；`release.json`：version 2.1.0、tag、commit、git_dirty=false、178 个文件哈希全部一致。随后按 Owner 裁决覆盖生产 `config/pkglist.txt`（5149 行），该文件哈希与 release.json 记录不同，属运行时 overlay，已写入目录内 `DEPLOYMENT-OVERLAY.txt`；其余文件仍与 release.json 一致。
- 工具自带测试：`84 passed, 1 skipped`（用 Python 3.13 + xlrd/xlwt/PyMySQL）。
- CLI：`-h` 含 `-merge_files_list`、`-match_issues`、`-dedup_org`、`-inc`；`-match_issues` 成功匹配 I1 / 新组 n1，非法输入 exit 2。
- `-dedup_org`：旧/新工具在同一 `_org.xls` 上的 xls 输出字节一致；新工具额外生成 `*_members.json`。
- members 校验：schema `stp.scan_membership` 1.0；org 行 8/8 全覆盖；`path` 与 `aeeexp` 表 Path 原文一致；org 报表 sha256 一致；`meta.tool.git_commit=9a1fdab…`、`git_dirty=false`。
- Run 1193 离线对照（12 个 MTK org 文件、161 条原始记录、生产 pkglist）：
  - 旧工具：14 个问题（MLD-NX2-16-261027V56），7 JE + 6 ANR + 1 other。
  - 新工具（含 `tools/`）：48 个问题（MLD-LX3 34 + MLD-NX2 14），并含 SWT/HANG/fatal JE 等类别。
  - 新工具去掉 `tools/` 的临时副本：14 个问题，与旧工具逐字节一致。差异来自新工具分析面（`BaseScan.py`/分析模块）在 `tools/` 可用时解析更多 JBT/SWT 行，不是 pkglist 配置。
  - 单轮 Run 1193（6 个 org、33 条）旧工具：11 个问题，与计划所述历史记录一致。
  - Unisoc 10 个 org、46 条：旧/新工具输出逐字节一致（7 个问题）。
- 结论：工具源码/CLI/成员文件通过；**merge 输出在 MTK 上不是逐字节兼容**，需要 Owner 判断新工具的多类别输出是否属于 B8 预期。

## 4. 执行时间线

1. 18:08 构建 `e48e6a43-20261104` bundle；venv 按 `requirements.lock` 安装，78/78 依赖版本一致；`import backend.main` OK。
2. 18:16 落位双版本工具目录；控制面 3.13 与 15.33 的 3.10 均实测可导入 xlrd/xlwt/PyMySQL，并可运行 `-h`。
3. 18:19 切换站点 env（四个工具键 + 影子 0/21600/7200），切换 `current` 到 `e48e6a43-20261104`，重启后端；18:19:46 起 `/health` 200、schema 在 head、影子 False、`supports_merge_files_list=True`。
4. 18:20 对 15.101 执行 `POST /hosts/172-21-15-101/hot-update?force=true`：`ok=true`、code `e48e6a43`、digest `bd3da535…`、`deps_refreshed=true`、env 键全部同步、无缺失路径；Agent venv `xlrd 2.0.2`；心跳 18:20:56、matched、23 个 ADB 设备、HEALTHY。
5. 18:21 对 Run 1127（plan 115，15.101/15.102 两台，终态 FAILED，3324 条 DLE）触发 `POST /plan-runs/1127/dedup/scan`。
6. 18:21–18:24 15.101 用新工具扫描；18:24:08 org 生成成功（1 条 fresh），随后 G3 映射失败：

   ```text
   ValueError: scan report serial outside run or unprovable at row 2
   ```

   该行携带 `ADDYNX6616000055`（15.101 当前设备），不在 Run 1127 的 40 台 `AYCGNX…` 设备集内。G3 契约要求报表 serial 必须是本 run 设备集子集，整轮拒绝，未上传、未登记任何产物。
7. 18:25 按计划 §7 回退：恢复 `env.backend` 备份、`current` 指回 `eeab11d8-20261103`、重启后端；18:25:53 起 `/health` 200、schema `b8g1c4a7d2e9`。
8. 18:26 对 15.101 回退热更新：code `eeab11d8`、digest `1067e716…`、env 旧路径、Agent venv xlrd 2.0.2；心跳 18:26:45、matched、23 个 ADB 设备、HEALTHY。
9. 回退重启使 SAQ 扫描任务在 18:26 重放；15.101 的扫描被重启打断，15.102 的旧工具扫描仍在运行。截至 18:33 未产生 artifact；Run 1127 的扫描验证据此标为 pending。

## 5. 金丝雀失败归因

- 直接原因：Run 1127 的旧报表/日志中存在 `ADDYNX6616000055` 设备行；该串号不属于 Run 1127 的 40 台设备。15.101 的 `scan_now` 载荷只包含 `AYCGNX…`。
- G3 行为符合契约（#3720 验收项）：`report_mapping` 对每一行做 serial 子集校验，发现越界即整轮失败，避免把跨 run/跨设备行上传。
- 该 run 为 2026-10-27 的终态旧 run，设备与日志已漂移；不是新代码引入的越界，但新工具的分析面比旧工具更容易把同目录内的其它设备行带进 org，离线 merge 的 MLD-LX3 34 行是同一类信号。
- 因此问题不只是“换一个 run”，还需要确认新工具在“同目录混有其它设备日志”时是否应过滤越界行，或 G3 是否应把越界行标为 unmapped 而不是整轮失败。这是 B8 工具/G3 契约层面的待裁决项。

## 6. 回退结果

| 项 | 结果 |
|---|---|
| 站点 env | 已恢复 `env.backend.bak-20261104-v210`（旧工具路径、无影子显式键；默认关闭） |
| 控制面 `current` | `/home/debian13/stp-releases/eeab11d8-20261103` |
| 后端 | active，`/health` 200，schema `b8g1c4a7d2e9` = head，`supports_merge_files_list` 走旧工具 |
| 15.101 Agent | code `eeab11d8`、digest `1067e716…`、env 旧路径、xlrd 2.0.2、matched、心跳 18:26:45 |
| 其余 76 台 Agent | 未更新，保持 BASE |
| 新工具目录 | 两侧保留（控制面与 NFS） |
| 新 bundle | 保留 `/home/debian13/stp-releases/e48e6a43-20261104` |
| 前端 | 未切换（目标仅依赖文件变化；本次不改前端入口） |
| 影子开关 | 关闭 |

## 7. L0–L3 验收状态

- **L0 身份与配置**：部分通过（回退后基线身份、schema、旧工具身份一致）；新版本身份未作为生产验收。
- **L1 运行事实**：未执行机队全量更新；金丝雀 Run 1127 未产生 artifact；标 pending。
- **L2 金丝雀行为**：**失败**。同机同轮 org 生成成功，但 G3 映射在第 2 行拒绝越界串号，未进入上传。
- **L3 反例证据**：引用 G2/G3 的 CI 与独立复核记录；本次生产环境未注入破坏性反例。

## 8. 未完成项

1. 机队 77 台的 G3 代码/工具更新（仅 15.101 更新后回退）。
2. 工具 v2.1.0 未在生产生效（新目录已备）。
3. 影子激活未开始。
4. 完整 B8 阶段一对拍（问题数、发生映射、代表齐全率、应上传字节）。
5. 15.102 旧工具扫描在 18:33 仍运行，Run 1127 的 current-chain 验证 pending。
6. 新工具 MTK merge 输出相较旧工具新增 MLD-LX3/ SWT/HANG 类别（48 vs 14），需 Owner 判定是否接受。

## 9. 越界行处理裁决建议（Owner 已选“先裁决再重试”）

**建议裁决：越界设备行不得上传，也不得静默标 unmapped 继续；G3 硬失败保留。**

理由：

1. B8 的目标是“纯 MTK run 的报表集合不混入其他设备”（计划 §5 L1）；把 `ADDYNX…` 行标 unmapped 后继续，org/xls 仍会携带该行，upload/merge 仍会混入，违反目标。
2. G3 当前在映射阶段整轮失败，是安全停止而不是缺陷；它阻止了跨设备行进入上传。
3. 短期重试应使用设备集与日志一致的清洁 run；不改代码、不改契约即可复用当前 v2.1.0 物料。
4. 长期修复归工具端：工具在 mode 0 也应接受 run 的设备串号过滤（新增 `-serial_filter`，或让 `-device_list` 覆盖 mode 0），由 Agent 传入 run 的 serials；G3 硬校验保留为纵深防御。该工具变更需要新的工具版本/tag，不与本次 v2.1.0 混做。
5. MTK merge 的 48 vs 14：多出的 34 行是 MLD-LX3 设备行，属于越界行；工具端 serial 过滤落地后会消失。若过滤后仍有 in-run 的 SWT/HANG/fatal JE 新类别，Owner 需单独裁决是否接受这些新类别（本次未裁）。

**短期重试选项与 Owner 选择**：

- **A（已选）**：等 15.93 的 Run 1203（单台、当前设备、24H Monkey）结束，更新 15.93 后用该 run 做单台金丝雀；当前 v2.1.0 物料可直接复用。Run 1203 于 2026-11-04 10:59:43 启动，预计 2026-11-05 11:00 后进入终态；恢复前需重新确认 Run 1203 的设备集与日志一致、15.93 空闲、无新的批次/迁移。
- **B**：在空闲主机上新建一轮当前设备的单台 MTK run（会真跑 monkey，需 Owner 明确接受设备占用）。
- **C**：先等工具端 serial 过滤版本（v2.1.x）发布后再重试；本次保持回退态。

## 10. 本轮收口

- 生产已回到 BASE，健康与 schema 正常；新 bundle 与两侧 2.1.0 工具目录保留，未删除任何轮次事实、映射、报表或台账。
- Run 1127 的 current-chain 扫描：15.101 的扫描被回退重启打断；15.102 旧工具进程在 18:33 仍在运行，未产生 artifact；标 pending，不属于本次新代码验收。
- 机队 76 台未更新；15.101 已回退。
- 恢复条件：Run 1203 终态且 15.93 空闲；重新读取现场并冻结 TARGET；若工具端先发布 serial 过滤版本，则按 C 重新备料。

---

_本报告只记录实际执行过的动作；未执行项均标 pending。_
