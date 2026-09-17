# 09-16 安装/升级链与城市 B 交付批次：工单收口记录（含 2 单重开残余）

Status: implemented
Class: process

## Decision

把 2026-09-16 这一批「Agent 安装/升级链 + 城市 B 现场交付」的工单收口状态固化成一份
**可追溯台账**（工单 → PR → 合并时间 → 状态 → 证据指针），并如实记录 **2 单被 owner 重开后
的残余**（24h 只读审计发现），避免后来者按「已关」推断现状。

### 已收口（issue 关闭，修复在 main）

| 工单 | 主题 | PR（合并 UTC） | 关键证据 |
|---|---|---|---|
| #2338 | 取消在 UI 落成红色「失败: CANCELED」→ 独立终态「已取消」 | #2339（09-16 08:06） | UI 端到端截图 + DOM 快照；hook/面板/HostsPage 新增 4 条用例；现场复验通过 |
| #2317 | 无 system D-Bus 目标时区对齐失败（容器重装被阻断） | #2356（09-16 10:30） | 容器重装 `SUCCESS / ok=30 failed=0`（现场） |
| #2319 | wrapper 前置判据自指：缺子命令的旧 wrapper 也放行 | #2356（09-16 10:30） | 机队 48/48 wrapper 升级并核验 `capabilities`；现场热更新 `converged: artifact digests matched` |
| #2404 | handover MS-01 证据 ID 不可满足（`install.s0` / `install.s3.migrate`） | #2407（09-16 12:53） | `handover` **7/7 PASS**（现场）；新增映射守卫测试 |
| #2410 | #2317 回归：`set_timezone.yml` 引用 rescue-only 变量 → 健康真机安装链必失败 | #2415（09-16 13:30） | 现场 S5 全绿（两真机 `ok=32 changed=6 failed=0`）；守卫测试 + 反向验证 |
| —— | 城市 B 现场复验结论（文档） | #2443（09-17 02:00） | [`process/2026-09-16-city-b-field-reverification`](./2026-09-16-city-b-field-reverification.md)（S0–S5、`handover` 7/7） |

### 重开 → 已收口（owner 2026-09-17 02:14 裁决；两单修复均已合入并关单）

| 工单 | 残余（收窄后） | 验收判据（审计评论原文要点） | 修复 |
|---|---|---|---|
| #2255 | 取消按钮的**渲染条件含 `pending`**（`HostOperationPanel.tsx:271-282`），而接口只作用于正在跑的 console run（`hosts.py:1206-1213`，无则 409）→ 并发闸门 2 之下批量安装时多数行停在 pending，点「取消」必得一条英文 `detail.message` 落到该行红字 | 渲染只看 `running`（或对 pending 明确禁用/给中文说明），且点取消不会在行内留下英文报错 | **已收口**：PR **#2486**（合入 2026-09-17 04:29Z，`25354e46`）——渲染收窄到 `running`、未受理/409 文案中文化；issue #2255 04:29Z 关闭 |
| #2315 | `export_kept` 分支（`stages.py:801-812`）只追加 PASS、**不做任何服务侧动作**；`systemctl enable --now nfs-server` 只在 `else` 分支（`:818`），且 `NFS_SERVER_UNIT` 全文唯一使用点就在这里 → 报「存储就绪」时不再确保 NFS 在服务 | `export_kept` 分支下若 `nfs-server` 未在跑：要么拉起、要么如实 FAIL/BLOCKED（不得以 PASS 掩盖服务未起） | **已收口**：PR **#2489**（合入 2026-09-17 04:43Z，`9b403586`）——kept 分支同跑 `enable --now`、拉不起来即 FAIL `install_export`；issue #2315 04:43Z 关闭 |

两单的完整审计备注与重开说明见各自 issue 评论（marker `audit24h-0917b-*`）；两个修复 PR 的
反向验证（退回实现 → 对应用例红）见 PR 描述与各自 issue 的后续评论。

### 无 PR 的操作项（生产数据/机队，留痕在评论与记忆）

- **机队 48/48 wrapper 升级**（`capabilities` 前置；#2356 落地前必须完成，否则热更新整批
  fail-closed）：canary → 备份 `/root/stp-agent-priv.bak-20260916` → copy + `bootstrap`（visudo
  校验）→ 核验 48/48 OK。
- **flash_preflight 版本收口**：`plan_step` 12/39/40/13 从 1.0.1/1.0.2 重指到 **1.0.4**
  （与 udev 0660/0666 双形态过渡相关）；退役判据：1.0.1/1.0.2 零引用但受 60 天留存窗口保护
  （约 10-29 / 11-14 到期），1.0.3 为当前唯一可退候选（留给下一批 #735 批次）。

## Alternatives

- **只更新「城市 B 现场复验结论」note（不新建台账）**：否决。那份 note 记的是**验收结论**
  （S0–S5 / handover / 证据索引）；本 note 记的是**工单生命周期**（谁关了、谁被重开、残余是
  什么）。两者交叉链接，合并会让「结论」被工单状态淹没。
- **只列已关闭工单**：否决。重开的 2 单正是最需要被后来者看到的部分——按「已关」推断现状
  会重复踩坑（尤其 #2315：`export_kept` 的 PASS 语义不全，是审计在 24h 内抓到的）。
- **把残余直接标为「已知、暂不修」**：否决。两条都有明确、低成本的验收判据（见上表），
  且 #2315 的 `enable --now` 缺口在 #2356 评审时已被提出、未落——记忆里应保留这条「评审
  意见未闭环」的信号。

## Verification

- 工单/PR 事实来自 GitHub API 逐条核对：issue 状态与 `closed_at`、PR `merged_at`
  （#2315/#2255 open 且带 02:14 reopen 事件；其余 closed）。
- 各 PR 合并时 required checks 全绿；现场证据见
  [`process/2026-09-16-city-b-field-reverification`](./2026-09-16-city-b-field-reverification.md)
  与 #2311/#2339/#2356/#2407/#2415 的评论。
- 两处重开残余均**在最新 tip 上复核仍存在**（审计用 `git diff --numstat` 对指定文件核过）。

## Revisit

- ~~#2255 / #2315 的修复 PR 合入后翻新~~（**已执行 2026-09-17**：#2486 合入并关闭 issue #2255、
  #2489 合入并关闭 issue #2315，上表已翻新为「已收口」）；若任一单被再次重开，按新残余重写该行。
- **每轮 24h 只读审计后**：把新开的残余单回填本台账（口径：工单 → 残余 → 验收判据 → 修复归属），
  避免审计结论只留在评论里。
- **下一批 #735 退役**：1.0.1/1.0.2 留存窗口到期（约 10-29 / 11-14）后执行退役并在此更新。
