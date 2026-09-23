# 第一性原理与长期复利审计正式稿落盘

Status: implemented
Class: process

## Decision

把跨链静态审计正式写入
[`PLATFORM_FIRST_PRINCIPLES_COMPOUNDING_AUDIT_2026-09-23_2bc172_codebuddy.md`](../../reviews/PLATFORM_FIRST_PRINCIPLES_COMPOUNDING_AUDIT_2026-09-23_2bc172_codebuddy.md)，
按「第一性原理（本质问题 / 成本量纲 / 复利三问）」组织为 F-01～F-13：三条 P0
（准入事务与心跳互锁、DB 连接总预算、产物丢弃不可观测）、新工具/新专项的复利出口
（F-04～F-07、F-09）、容量与治理成本项（F-10～F-13），并给落地顺序与可度量验收方向。

只新增两份文档；不修改业务代码、ADR、生产配置或已有审查稿；不新建 issue。

## Alternatives

- **照抄同日并行稿（codex 会话）**：不采纳。按「会话×模型」为独立单位，本稿独立取证，
  只在 §5 给对照；两稿一致处互为佐证，互补处并表。
- **只给容量结论、不写新增面（工具族/资产/平台词表）**：不采纳。本轮需求增量恰在
  "持续新增专项/工具/项目"，复利负债主要在新形态的接入成本上。
- **把取证基线上的 F-08 原样落盘**：不采纳。落稿复核发现 ADR-0051 Phase 3 已合入
  （#3208），F-08 按新基线改写为「已收口 + 残留」，并把当日暴露的 F-09
  （族树扁平化导致相对资源路径错位）单列待真机复核。
- **顺手修 F-03 / F-09**：不采纳。方向级/跨模块改动应另立单；本稿只交付证据与出口。

## Verification

- 取证基线与复核：主体 `main@512e61a8`；落稿复核 `main@8bc6bc1e`（Phase 3 落地）。
  以 `git diff 512e61a8..origin/main` 复核引用锚点——除 `admission_pump.py` +4 行
  （Phase 3 补推跳过 `package_active`）外，F-01～F-07、F-10～F-13 的引用文件未变动。
- 关键结论逐条 `file:line` 复核（择要）：`admission_pump.py:82-105`、
  `heartbeat.py:135,535`、`database.py:156,257-258`、`deploy/postgres/docker-compose.yml:31`、
  `artifact_uploader.py:277-285`、`heartbeat_bindings.py:111-121`、
  `dedup_platform.py:5-6,11-18`、`check_new_script_family.py:18-24`、
  `verify_tool_contract.py:47`、`tool_cache.py:199-205`、
  `flash_firmware.py:867-870`、`install_agent.sh:250`、`gpu_setup/_lib.py:245`。
- 落稿前运行 `python scripts/run_gates.py check:quick`（15 gates），结果见 PR。
- **F-09 追加复核（2026-09-23，只读）**：① 机队 ad-hoc（`agent_config` 48/48 台，`-m shell`
  只读）三查：`<install>/resources/flashtool` 缺失 48、`STP_FLASH_TOOL_DIR` 未设 48、
  工具实际在 `<install>/agent/resources/flashtool` 48，且 `STP_SCRIPT_PACKAGES=strict`、
  cache 最新 1.3.17；② 控制面只读 API 核对 7 个在库刷机计划的步骤参数与最近一次刷机
  run 509 的 `plan_snapshot`——均无 `flash_tool_dir`；③ 站点包源 `tar tzf` 确认包根即
  `flash_firmware.py`（包模式上跳 3 层落在 `<install>`）。结论：F-09 坐实、潜伏未爆发
  （最近刷机 run 2026-09-22 13:46，在 Phase 3 之前），失败为安全失败（不碰设备）。
  复核所用临时凭据文件已删除，未写任何仓库外持久状态。
- Pending（本轮未做，不得记为通过）：backend/agent/前端全量测试、真机 25 台与
  150/3750 分档验收、F-09 修复后的真机 flash 复核、mtbf setup 同类复核、
  生产只读容量复核、issue 状态复核。

## Revisit

F-01/F-02/F-03 处置（或 ADR-0047 裁决、#2959 关闭）后按新 `main` SHA 复审；
F-09 修复（新版本 + 7 个在库计划重指）落地后做一次真机 flash 复核并回填；
F-08 残留项在采集到"横切修复 × 族数"分布后决定是否复议共享基座。
不原地改写本稿取证基线与历史结论。
