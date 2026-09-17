# 城市 B（238）现场复验结论（2026-09-16）

Status: implemented
Class: process

## Decision

**结论：城市 B（`city-b`，站点 238）的 P1 现场复验完成** —— 站点升级到含
`#2315` / `#2317` / `#2319` / `#2404` / `#2410` 的发布物后，安装链（S0–S5）与
交接证据（`verify` + `handover`）全部按设计收敛：

| 面 | 结论 | 证据（站点产物/命令） |
|---|---|---|
| 控制面安装 | S0–S5 全 PASS（`runs` 累计 48+，含一次性迁移 `install.s3.migrate` 与随后幂等 `install.s3.db schema_at_head` 两条路径） | `install-state.json`、`/root/install-int-s5.log` |
| Agent 接入（S5） | 两台真机 `install_succeeded` / `agent_online` / `identity_recorded`；`agent_artifact_digest` 与清单一致 | 同上；15.12 `ok=32 changed=6 failed=0`、15.13 同 |
| 中心存储 | 导出给 Agent 网段（`all_squash,anonuid=1000`，客户端清单来自站点声明）；两台真机写探针 WRITABLE；**不带清单的幂等升级报 `export_kept` 且导出表逐字未变**（#2315/#2356 修复） | `exportfs -s`、`/etc/exports.d/stp-city-b.exports`、`install.s2.export` |
| 监控栈 | `/-/ready` 就绪、node-exporter 被刮取、`/storage` 有数据源（延续 2026-09-15 结论） | `install.s4.monitoring` |
| 时区 | 控制面/声明/Agent 三面同源 `Asia/Shanghai`；健康真机与无 bus 容器（文件级回退）两条路径都 PASS（#2410 修复后） | `install.s1.timezone`、容器重装 `SUCCESS` |
| 提权边界 | 两台真机 wrapper 带 `capabilities`（14 子命令）→ `POST /hosts/{id}/hot-update` 收敛（`artifact digests matched`） | #2319/#2356 |
| 取消入口 | 无在跑→409 留痕；在跑→`CANCELED`（`rc=-15`）+ 审计两条 + 复跑无 409；UI 端到端「已取消」（#2255/#2339） | `install_agent_cancel` / `install_agent` 审计 |
| 交接证据 | `verify`：auth / csrf / **hosts(2 declared ONLINE)** / navigation / devices / chain / storage(写读探针) 全 PASS；`handover`：**MS-01/02/04/05/06/10/13 共 7/7 PASS**（MS-01 经 #2404 修复后转正） | `/var/lib/stp/verify-report.json`、`/var/lib/stp/handover.json` |

**随结论落地的两条站点侧收敛**（不是代码变更，但决定结论可复现）：

1. **Agent 声明沉淀进 `site.yaml`**（`agents:` 两条，凭据只写引用 `agent_ssh`）——此前声明只活在
   per-run inventory，是「升级不带清单清空导出」「`verify.s6.hosts` 空过」「handover 证据薄」的
   共同根因；沉淀后不带 `--agents-inventory` 的升级也保持导出与断言对象（`export_kept` 属兜底）。
2. **站点发布物**：本轮验证用 `local-20260916-381b405c`（= 当时的 `main` + `#2404` + `#2410`
   集成；两个修复随后分别以 PR #2407 / #2415 合入 main）。下一次站点升级建议直接用 main 重建
   bundle，使 `release.expected_release` 对齐 main 提交。

**仍待办（本结论不覆盖）**：

- `verify.s6.scan_upload_merge` 与 `verify.s6.watcher` 仍是 BLOCKED（需真实设备日志 / 观察窗口）；
- MS-01 的「非原作者按指南接入受控设备」与签字（现场人工项，`handover` 的 `pending` 已列明）；
- 城市 C：未开始（无站点输入与机器）；
- 2 个 I4 容器 Host（I4 实验室）的退役/改造裁决（重装已可用——容器路径本轮已验通）。

## Alternatives

- **直接给 PRD 的 MS-01–MS-15 表逐行标 PASS**：否决。该表覆盖多城市与多人协作的验收面，
  签字是现场动作；逐行标 PASS 会把「一个人的复验记录」冒充成整体验收结论。本 note 记录
  复验事实，逐项签字仍按 [`site-handover-and-navigation.md`](../../operations/site-handover-and-navigation.md) §3 的清单执行。
- **只在 PR 评论里留证（不加 note）**：否决。证据散在多个 PR（#2255/#2339/#2407/#2415/#2315/#2317）
  与站点产物里，下一次现场复验需要一份「去哪看什么」的索引；note 承担这个索引。
- **把 `agents:` 只留在 per-run inventory**：否决。声明不沉淀会让每次升级都依赖「记得带清单」，
  并对 `verify`（无 inventory 入参）产生「0 declared Agents 空过」的假证据。

## Verification

- 站点产物（权威、可复跑）：`/var/lib/stp/install-state.json`、
  `/var/lib/stp/verify-report.json`、`/var/lib/stp/handover.json`；
  `/site/handover.json` 可经站点入口下载（导航页固定链接）。
- 命令与结果：
  - `./deploy/install.sh --yes --through-agents` → `rc=0`，S0–S5 全 PASS（日志 `/root/install-int-s5.log`）；
  - `/opt/stp-tool/bin/python -m tools.site_config verify --config /etc/stp/site.yaml --bindings-dir /etc/stp/bindings --storage-probe-subdir probe --json` → `rc=0`；
  - `./deploy/install.sh handover` → `handover.MS-01…MS-13` 7/7 PASS；
  - 幂等（不带清单）`./deploy/install.sh --yes` → `install.s2.export [export_kept]`，`exportfs -s` 未变；
  - 容器重装 容器 Host 的重装接口（`POST /hosts/<host>/install`） → `SUCCESS / exit 0 / ok=30 failed=0`；
  - 热更新 真机热更新接口（`POST /hosts/<host>/hot-update`） → `converged: artifact digests matched`。
- 缺陷与修复（本轮现场发现并已合入 main）：
  - **#2404**（handover MS-01 证据 ID 与 emitter 脱节）→ PR #2407；
  - **#2410**（#2317 回归：`set_timezone.yml` 引用 rescue-only 变量，健康真机安装链必失败）→ PR #2415；
    两个修复的反向验证（退回实现 → 对应用例红）见各自 PR。
- 反例记录（避免下次踩）：站点安装器与 console 安装**不可并发**（S4 重启后端会取消在跑
  RunConsole，ADR-0044 边界）；bundle 目录内跑 Python 会生成 `__pycache__` 触发 S0 卫生守卫
  （用 `PYTHONDONTWRITEBYTECODE=1` 或改在别处执行）。

## Revisit

- **每轮站点升级后**：重跑 `verify` + `handover` 刷新证据（本 note 的矩阵按同样口径复评）；
  若某个 MS 项从 PASS 退回 BLOCKED，先查对应检查的输入是否变化（声明/发布物/目标机状态）。
- **`scan/upload/merge` 专项落地后**：在站点上跑一次真实设备日志流程，把
  `verify.s6.scan_upload_merge` 从 BLOCKED 转正，并回填本矩阵。
- **城市 C 启动时**：把本 note 当作复验脚本的模板（哪几个面、看哪些产物、哪些是人工项）。
