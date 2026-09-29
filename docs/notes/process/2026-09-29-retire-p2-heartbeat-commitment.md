# 退役 P2 心跳接线承诺：契约 v1.15 / ADR-0034 v1.14（#3516）

Status: implemented
Class: process

## Decision

Owner 于 2026-09-29 在 #3516 裁决：**退役 `whoami` / `heartbeat` 作为 P2 Adapter 的自动接线承诺，CLI 命令暂保留**。按契约
「先改契约再改实现」的纪律（`execution-contract.md` §10），本 PR 是该裁决的版本化修订，不通过 AGENTS.md 静默改变语义：

- `execution-contract.md` → Living v1.15：§4 由「TTL 与心跳分期」改为「TTL 与 last_seen 语义」；§1.2 `last_seen` 与 §3.1
  `STALE` 措辞收窄为「最近一次 Registry 写动作 / 手动 heartbeat 时间」「只表示久未写」；v1.14 头部明细迁入附录 A.4，
  正文字节数由 24053 增至 24454（预算 24500），未抬预算；
- `harness-adapters.md`：「P2 Adapter：会话启动动作」改为「Registry CLI 手动入口（非 Adapter 义务）」，`whoami` /
  `heartbeat` 行不再写「启动 / wrapper 定时调用」「升格为可靠 liveness」，「所有 Harness 通用」一列改为「说明」；
- ADR-0034 → v1.14：修订记录、§2.5 决策要点、分期表 P2 行、Alternatives 与 Verification 中的 P2 心跳条款；
  **P2 的另一半——cwd 深度 × Harness 加载矩阵验收——保留**（#3516 G2 承接）；`adr/README.md`、`DOC-MAP.md` 同步；
- `tools/dev/ai_work.py` 仅改两处注释 / docstring（不改行为、不改子命令）。

**不变**：Registry 字段集、三维状态、overlap 谓词（liveness 本就不参与，§3.2）、transition table、僵尸候选判据
（`STALE` 仍作 advisory 输入）、`whoami` / `heartbeat` / 无参 `update` 命令本身。

## Alternatives

- **接线**（Claude / Codex hook 调用，GUI Harness 缺位）：否。仅部分 Harness 有脚本通道（`harness-adapters.md` 已记录
  Zcode、CodeBuddy IDE、Antigravity 的限制），部分接线得到混合信号，比明确「没有统一 liveness」更容易误判；
  且 liveness 不参与 risk 判定，价值不足以支撑一套跨 Harness 常驻机制（ADR-0058 D6：无复发证据不预建机制）。
- **立即删除 CLI 命令**：否。`whoami` 仍是有用的手动只读诊断，`heartbeat` / 无参 `update` 有兼容用途；
  长期无真实消费者再按退役扫描删除，届时另走契约版本化。
- **把 `last_seen` 从 Registry 字段中移除**：否。字段集封闭（§3.6），且僵尸候选判据依赖 STALE；本次只收窄语义。

## Verification

- `python tools/dev/check_governance_surface.py --check --base origin/main` 与 `--self-test`：通过（含 S12 ADR 索引、S13 契约版本
  一致、S15 归属表）；
- `python tools/dev/ai_work.py --self-test`、`py_compile`：通过（仅注释 / docstring 变更）；
- 契约正文 198 行 / 24454 字节（预算 210 / 24500），`harness-adapters.md` 99 行（预算 100）；
- 残留扫描：`grep` 「可靠 liveness」「wrapper.*heartbeat」「P2 Adapter」在现行文档中无剩余承诺性表述
  （历史记录与归档不回改）；
- 未验证：`check:quick`（云端容器无 `psycopg`，交给 CI）；`ai_work.py` 的 pytest 用例（系统解释器无 pytest）。

## Revisit

- `whoami` / `heartbeat` 长期无真实消费者时，纳入 #3516 G4 的退役扫描，届时另出契约版本；
- 若日后需要「在线状态」，先证明有真实需求（某机制需按存活区分行为），再以契约新版本 + ADR 增补重新设计，
  不恢复本次退役的 wrapper 模型；
- 云端实施者的可见性由 #3516 待落地的 ADR-0058 / ADR-0034 修订另行裁决，本 note 不涉及。
