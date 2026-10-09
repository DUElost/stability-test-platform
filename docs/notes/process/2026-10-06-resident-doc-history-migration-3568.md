# G4 剩余清理：Python/CI 版本叙述、Cursor CLI 状态、贴顶文档的历史与过程记录迁出（#3568 / #3516）

Status: implemented
Class: process

## Decision

Owner 2026-10-06 裁定启动 G4 剩余清理：仍由 [#3568](https://github.com/DUElost/stability-test-platform/issues/3568)
承载，开一个独立 draft PR；范围限定为下面三类，**保留证据出处和治理锚点，不新增机制，不扩大到 #3602**。
每一处都先用代码、CI 配置或证据评论核实，再改文字。

1. **Python/CI 版本叙述**（`dependencies-and-quality.md`）。原文「CI 各 job 统一 Python 3.11（`lint` /
   `pr-compileall` / `pr-agent-tests` / `pr-typecheck` 同版 …）」有两处与事实不符：`pr-typecheck` 是 Node 22 的
   前端 TypeScript 检查，根本没有 `setup-python`；`pr-agent-tests` 除 3.11 外还有一步 3.10 下限解释器上的安装器闭包
   导入（#2268，版本与 `preflight.MIN_PYTHON` 一致并由 `tests/test_site_installer_python_floor.py` 钉住）。已按
   `ci.yml` 逐 job 的 `setup-python` 版本改写；「本机 venv 是 3.13」与 3.12+ 语法差异的结论不变。
2. **Cursor CLI 陈旧状态**（`harness-probes.md`「已知不可跑形态」）。原文写「该格恒 UNVERIFIED。这是接受的终态 …
   真实证据需在有预置工作区信任的宿主上复跑」。2026-10-03 的事实是：旧账号团队额度拒绝时六格 UNVERIFIED
   （[证据](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5965465667)），同日换账号后
   同命令六格 PASS（[证据](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5965565067)）。
   改为「结果随宿主条件变化」：保留 Owner 2026-10-01 对 UNVERIFIED 的裁决、不加信任绕过参数、两轮证据链接，并写明
   PASS 只代表该宿主 / 日期 / CLI 版本。
3. **贴顶文档的历史与过程记录迁出**（S6 预算：`harness-adapters.md` 100 行 / 10000 B，`execution-contract.md`
   210 行 / 24500 B）。迁出的是叙事，不是条款：
   - `execution-contract.md` 四处：§3.5 的事故叙述两处（附录 A.6 已有同一段原文，只删正文副本，留指针）、§5.2 的
     实测反例（迁附录新增 A.7）、§8 的「原 ≈2-3 上限移除经过」（并入附录 A.4 的 v1.7 条目）。正文条款与 S13 锚点
     （状态行 `Living v1.16`、版本记录首项、附录版本、DOC-MAP 行）一字未动。
   - `harness-adapters.md` 两处：Zcode 3.14.4 六格报告的过程细节（取证 / 判卷 HEAD、人工等价判卷的更正链接）、
     已被取代的 2026-09-04 并行约定的说明句。原文**逐字**保存在下面「迁出原文」。迁出同时修正一处已陈旧的状态：
     原文「已知利益冲突，独立核实/重验 pending」，Owner 2026-10-03 已裁决无需独立重验（披露保留，不改写为独立证据）。

**不改版本号**：本次不改任何条款，只迁出叙事。先例：`2cc39401`（收紧 #3539 措辞）、`bc8906a9`（收口 5 处协调域残留）
同样在不 bump 的前提下改过契约正文。若 Owner 认为迁出也应走 v1.17，是一次机械的后续（状态行 / 附录 / DOC-MAP 三处）。

**没有动**：根 `AGENTS.md` / `CLAUDE.md` / `backend/agent/AGENTS.md`（不在范围、也没有可迁的历史）；契约正文里
`（vX.Y 增，#N）` 这类行内版本标记（它们标注的是仍然有效的条款，附录 A.4 只是反向索引）；#3602 的残留议题；周退役
扫描（仍只归 #3534 Unit 4.2）。

## 迁出原文

### `harness-adapters.md` 原文 A（「仓库内适配面」表后的退役说明段，原第 33–37 行）

```text
**Cursor IDE / CodeBuddy IDE 已于 2026-10-01 退役**（Owner 裁决）：不再参与 #3516 G2 的
验收矩阵，人工通道收敛为 Zcode 三格；Cursor 的 `.cursor/rules/` 适配面与 CodeBuddy **CLI**
行不受影响，历史实测保留在 ADR-0034 附录 A 不再复测。**Zcode 行的版本证据不可跨版本搬运**——
3.14.4 六格报告（2026-10-02）：实际取证 `af4edf99`；判卷 HEAD `bb1f34d9`，root_version 不一致先判 UNVERIFIED；
Owner 批准核实仅两份无关测试变化，契约/探针未变，人工等价判卷（[更正](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5948070189)） contract/autoload 各 3 PASS。agent/aee Q1 是/否 → workspace-only，contract PASS ≠ 自动注入；**已知利益冲突，独立核实/重验 pending**。
```

保留在正文的部分：退役说明、Zcode 版本证据不可跨版本搬运、3.14.4 contract/autoload 各 3 PASS、agent/aee
workspace-only 的结论、利益冲突已披露及 Owner 2026-10-03 裁决，并指回本 Note。迁出的是：取证 HEAD `af4edf99`、
判卷 HEAD `bb1f34d9`（root_version 不一致先判 UNVERIFIED）、「Owner 批准核实仅两份无关测试变化，契约 / 探针未变，
人工等价判卷」及其[更正评论](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5948070189)。

### `harness-adapters.md` 原文 B（「修改顺序」节末，原第 65–68 行的后半）

```text
（Accepted）与 [`execution-contract.md`]{execution-contract.md}；
[`2026-09-04-multi-agent-parallel-convention.md`]{../../notes/process/2026-09-04-multi-agent-parallel-convention.md}
已被取代，其元文件串行化实践继续有效，派生视图保留为 ground truth 交叉验证
手段（契约 §5.1/§9）。
```

上面两处 `]{…}` 在原文里是 Markdown 链接目标 `](…)`；这里改成花括号，是为了让链接检查不把它们按本 Note 所在目录
解析。被取代的约定文件仍是 `docs/notes/process/2026-09-04-multi-agent-parallel-convention.md`（头部有取代注记）。
「元文件串行化」已由同段首句保留，「派生视图为 ground truth 交叉验证手段」在契约 §5.4 / §9 有权威表述。

### `execution-contract.md` 迁出对照

| 原位置 | 原文（删除或迁出的部分） | 现位置 |
|---|---|---|
| §3.5 首段 | 「同一 Requirement 的两个独立 Execution 把结论落成同一编号 ADR-0035 的两份文件，两份均自称权威；」 | 附录 A.6 原文已有（「各自把结论落成同一编号 ADR-0035 的两份文件 … 两份正文均自称权威」），正文留 #906 / 日期 / 指针 |
| §3.5 纪律 1 | 「——本次事故中第二个 Execution 未声明 issue，§3.4 因此静默通过、只剩 hint 级 overlap。」 | 附录 A.6 原文已有（「第二个 Execution 未声明 issue，§3.4 查重因此静默通过、只剩 hint 级 overlap」），正文改为「（附录 A.6 的事故即此形态）」 |
| §5.2 末 | 「实测反例（为何 drift 提示必须存在）：2026-09-04 `docs/drift-sync-*` 声明 `docs`、实际 diff 触及 `backend/` 与 `.github/`——并集 + drift 提示使两个方向都可见。」 | 附录新增 **A.7**（原文逐字），正文留指针 |
| §8 首句 | 「原「≈2-3 显式上限」自 2026-09-04 约定未实测继承、被多 Harness 批次 5+ 会话常态超出（含单 Harness 多开），于本版移除（理由与守对象重锚见 ADR §2.6 v1.9）。」 | 并入附录 A.4 的 v1.7 条目，正文改为指向 A.4 v1.7 与 ADR §2.6 v1.9 |

### 预算变化（S6）

| 文件 | 迁出前 | 迁出后 |
|---|---|---|
| `harness-adapters.md`（预算 100 行 / 10000 B） | 100 行 / 9999 B | 99 行 / 9680 B |
| `execution-contract.md`（预算 210 行 / 24500 B） | 200 行 / 24366 B | 200 行 / 24062 B |
| `execution-contract-annex.md`（预算 200 行 / 20000 B，非常驻） | 93 行 / 10170 B | 97 行 / 10656 B |

两份常驻文档各腾出约 300 B（adapter 另少 1 行）——量级有限，是「迁叙事、不动条款」的自然结果，不是预算问题的根治。

## Alternatives

- **一次性删掉契约正文里全部 `（vX.Y 增，#N）` 行内标记**：能腾出更多预算，但这些标记标注的是仍然有效的条款，批量
  删除会改动整份唯一权威源里约 25 行（32 处标记），评审成本与误伤风险都远大于收益；不在「迁叙事、不动条款」的范围内。
  若以后确实需要更多空间，它仍是下一个候选。
- **把 Zcode 3.14.4 的过程记录迁进 ADR-0034 附录 A**：附录 A 是 harness 摄取实测矩阵，位置合适；但改 ADR 会牵出
  S12 的版本 / 索引同步，而本次不改任何决策。放进带 Status / Class 头的 Note 即可保留出处。
- **为 Python 版本叙述新增一条「文档与 ci.yml 一致」的守卫**：违背「不新增机制」；事实源就是 `ci.yml` 与
  `preflight.MIN_PYTHON`，已有 `tests/test_site_installer_python_floor.py` 钉住 3.10 一侧。
- **把 Cursor CLI 的「已知不可跑」整条删除**：丢掉 Owner 2026-10-01 的裁决与可复现的两轮证据；改为条件化表述
  更准确。

## Verification

- 事实核对（不凭记忆）：对 `.github/workflows/ci.yml` 逐 job 解析 `setup-python` 版本——`backend-test` /
  `pr-backend-tests-informational` / `lint` / `pr-migrate-empty-db` / `pr-compileall` 为 3.11，`pr-agent-tests`
  为 3.10 + 3.11 两步，`frontend-check` / `pr-typecheck` / `docker-build` 无 Python 步骤；`pr-typecheck` 的步骤是
  `setup-node` 22 + `npm run type-check`；`ruff.toml` 为 `target-version = "py311"`，`MIN_PYTHON = (3, 10)`，
  `Dockerfile.backend` 基镜像 `python:3.11-slim`。
- Cursor 两轮证据逐条读过原评论：旧账号六次调用均 `exit=1`、stderr 为团队额度上限；换账号后 contract 3 / autoload 3
  PASS，CLI `2026.09.18-9a7762b`。
- 迁出对照：A.6 已含两处被删的正文副本（上表）；A.4 的 v1.7、A.7 为新增或补充的原文。
- 治理锚点：S13 状态行 / 版本记录首项 / 附录版本 / DOC-MAP 行未动。`check:quick`：**16 gates OK**，其中治理面
  S1–S15 / S5x 全绿（含 S6 预算、S10 Note 头、S2 相对链接目标存在性）；首次运行曾因本 Note 的逐字引用里有一个相对链接
  按 Note 所在目录解析而 S2 报断链，已把引用里的链接目标改写成花括号后复跑通过。S2 不校验 `#标题` 片段，所以契约
  → 附录的 7 个标题锚点（含新增 A.7）另用 GitHub slug 规则脚本逐条核对，全部命中。
- 与在途 PR 的文本合并：`harness-probes.md` 同时被 #3604（OpenCode 退役，2026-10-06 已合入 main）与 #3611（原因码，
  在途）修改，改动位置互不相邻。本分支在含 #3604 的 main 上变基后复跑 `check:quick`；与 #3611 的头做 `git merge-tree`
  无冲突，三者（本分支 + #3604 + #3611）依次合并同样无冲突。
- 未验证：没有重跑任何真实 CLI 会话（Cursor 的结论引用既有证据）；required CI 与独立复核 pending。

## Revisit

- 契约与 adapter 仍然贴近预算。下一个迁出候选：契约正文的行内版本标记、adapter 表格里带日期的探针过程记录
  （CodeBuddy CLI / dsh web 行）。动手前先确认它们不是仍然有效条款的唯一出处。
- Cursor CLI 的结果取决于宿主账号与额度，探针本身不变；任何一次新取证都应按「宿主 / 日期 / CLI 版本」记在
  `harness-probes.md` 这一处，而不是散落到别的文档。
- `harness-probes.md` 的 Cursor 条目写的是 2026-10-03 的事实；若 #3516 母单关闭时仍保留该段，应确认它没有再次变旧。
