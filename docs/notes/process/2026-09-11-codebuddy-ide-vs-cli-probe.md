# CodeBuddy IDE 加载行为实测——与 CodeBuddy CLI 的区分（ADR-0034 附录 A 输入）

Status: implemented
Class: process

## 背景

用户问「CodeBuddy IDE 是否兼容 ADR-0034 的多 Harness 策略」。**此前仓库把
CodeBuddy CLI 与 CodeBuddy IDE 混为一谈**：`harness-adapters.md` 只有一行
`CodeBuddy（2.143.1）`，其结论（「根+嵌套均自动装载」）实际只来自 CLI 探针，
却被读作覆盖整个 CodeBuddy 产品线；`tools/dev/harness_probe.py` 也只有
`codebuddy`（=`codebuddy -p`，CLI）一个形态。IDE 从未被任何探针覆盖。

两者是分立实体：CLI 为 npm 包 `@tencent-ai/codebuddy-code`（本机 2.149.0，
`codebuddy`/`cbc`/`codebuddy-code`）；IDE 为 VS Code fork（`~/.config/CodeBuddy CN`、
`~/.codebuddycn`、`~/CodeBuddy`）。同厂商不代表同加载通道——ADR-0034 附录 A 已
有先例：Cursor 分列 `Cursor Agent`（CLI）与 `Cursor IDE 3.17.19` 两行，即便二者
同引擎亦单独实测、单独记录。

## 实测

- **协议**：ADR-0034 附录 A / G2 试点同源双题探针（cwd=`backend/agent/`，
  禁用工具，Q1=根契约『## 总原则/## 提交前』标题可见性、Q2=scoped 真身
  『Agent 侧 scan / upload』标题可见性、Q3=scoped 内容出现次数）。
- **通道**：GUI 人工探针（IDE Agent 面板无脚本通道，与 Zcode 3.11.2、dsh web
  同形态）；子目录 `backend/agent` 作为工作区打开。
- **样本**：本机 CodeBuddy IDE（2026-09-11 会话）。

| 探针 | 结果 | 判读 |
|---|---|---|
| Q1 根契约 | **否** | 根 `AGENTS.md` **不注入**；上下文中的「（总原则/8 条硬不变量）」是 scoped 真身第 3 行自身的行内提及，非根契约标题注入（已核 `backend/agent/AGENTS.md:3`） |
| Q2 scoped 真身 | **是** | `backend/agent/AGENTS.md` 经项目指引注入 |
| Q3 出现次数 | **一次** | 单份加载（无 symlink 双份问题，与 Cursor IDE 的 Q3=2 对照） |

**结论：CodeBuddy IDE = Zcode 形态**——子目录打开只装载 workspace 的 scoped
`AGENTS.md`，根契约不注入（Q1=否/Q2=是），与 CLI 的「根+scoped 双边可见」相反。
「总原则」在引述文字层面仍可见（scoped 真身头部含根指针），与 Zcode 的
「可发现性由 scoped 真身根指针覆盖」同构。

**与 CLI 的对照**（CLI 侧本次复测已于同日通过，见下）：

| 实体 | 版本 | Q1 根契约 | Q2 scoped | Q3 | 通道 |
|---|---|---|---|---|---|
| CodeBuddy **CLI** | 2.149.0（文档记 2.143.1，已滞后） | 是 | 是 | 一次 | `codebuddy -p`，可自动化 |
| CodeBuddy **IDE** | 4.11.3（人工读取 Help→About） | **否** | 是 | 一次 | GUI 人工探针 |

- CLI 复测证据：`python3 tools/dev/harness_probe.py --only codebuddy` → **PASS**
  （Q1=是 Q2=是）；另以 `cwd=backend/agent` 手工复跑一致。独立机制证据：
  `~/.codebuddy/logs/` 的 `[MemoryLoader] Loaded 1 memory rules: [project] [always]
  .../AGENTS.md`。
- **版本漂移**：适配表记 `2.143.1`，本机实测 **2.149.0**；且 2026-09-06 日志已见
  `"user-agent":"CLI/2.143.0 CodeBuddy/2.143.0"`——即文档所记版本从一开始就偏早。
  本次一并校正。

## Decision

**CodeBuddy CLI 与 CodeBuddy IDE 是两个分立实体，不得互相外推结论**：附录 A 与
`harness-adapters.md` 的原单行「CodeBuddy」实为 CLI 探针结论，却被读作覆盖整个
产品线，而 IDE 从未被任何探针覆盖。故照 Cursor CLI/IDE 分列先例拆为两行：

1. **CodeBuddy CLI**（`codebuddy -p`）：根+scoped 双边可见、单份加载、零配置、
   Registry CLI 与 P2 动作表全程可用——维持原结论，版本按实测校正为 2.149.0；
2. **CodeBuddy IDE**（GUI 人工探针）：**Zcode 同形态**——子目录只装载 workspace 的
   scoped `AGENTS.md`、根契约不注入（Q1=否/Q2=是/Q3=一次）；无脚本通道，
   Registry CLI 未 dogfood、**不随 CLI 转正**。

同步落点：ADR-0034 v1.12 附录 A 两行 + 版本记录、`harness-adapters.md` 两行、
`harness_probe.py` 拆出 `codebuddy`（CLI，自动）与 `codebuddy-ide`（人工）两形态、
`adr/README.md` 主表与 M7 行、`DOC-MAP.md` 行（S12/S13 口径）。本 note 为实测证据母本。

## 边界

- IDE 版本号=**4.11.3**：经用户人工读取（Help→About）补入。磁盘侧无法取——`product.json`/
  `package.json` 不在预期路径，`logs/` 仅有 8 月会话残留的 4.11.2（早于当前版本，
  与版本已升级一致）。故版本号来源为人工读取，非自动取证，已如实标注。
- 未验证 IDE 是否存在独立规则通道（VS Code fork 的 workspace 信任 / 扩展注入 /
  `settings.json` 路径）。本次只做附录 A 黑盒行为观测，不白盒推断加载机制
  （探针第一性设计：检测加载结果，不推断机制）。
- Registry 侧无需改动：`--harness` 为自由文本且刻意不做名单硬校验
  （ADR-0034 v1.6），IDE 若转正不必改代码。

## Alternatives

- **沿用 CLI 结论覆盖 IDE**——否决：正是本次要修的误并；Cursor 先例证明同厂商/
  同引擎亦须分列实测。
- **不实测即标注「IDE 兼容」**——否决：`harness-adapters.md` 明文禁止仅凭文件名
  或同族推断规则生效。
- **IDE 与 CLI 合并为一行加注**——否决：两者结论相反（Q1 是/否），合并必然误导；
  分列才是可判读形态。
- **本次一并把 IDE 登记为可承接 Requirement 的 Harness**——否决：单次探针只证
  规则加载，未证 Registry CLI 全周期 dogfood（对照 dsh web 转正需真实单）；
  且 GUI 无脚本通道，其 Execution 周期可行性未验。

## Verification

- 附录 A 协议人工探针实测：Q1=否 / Q2=是 / Q3=一次（cwd=`backend/agent`，CodeBuddy IDE GUI 会话）；
- CLI 对照：`python3 tools/dev/harness_probe.py --only codebuddy` → **PASS**；
- `python3 tools/dev/harness_probe.py --self-test` → 通过（判卷/偏离/FORMS 完整性红绿双向；本单将「唯一人工形态=zcode」的硬编码改为 `manual` 标志，使 GUI 形态可扩展）；
- `python3 tools/dev/ai_work.py --self-test` → 通过；
- `python3 tools/dev/check_governance_surface.py --check` → 通过（S1–S13、S5x 全绿）；
- `python3 -m pytest tests/ -k "harness or gov or ai_work"` → 3 passed；
- `venv/bin/ruff check tools/dev/harness_probe.py` → All checks passed；
- 文档同步：`harness-adapters.md` CodeBuddy 行拆为 CLI / IDE 两行（压回 S6 100 行预算内），
  `tools/dev/harness_probe.py` 拆出 `codebuddy`（CLI，自动）与 `codebuddy-ide`（人工）两形态，
  ADR-0034 v1.12 + `adr/README.md`（主表 + M7）+ `DOC-MAP.md` 索引同步；
- IDE 版本 **4.11.3** 经人工读取（Help→About）补入——ADR 附录 A / 适配表 / 探针形态 /
  本 note 四处一致；
- **未完成项（如实标注）**：`check:quick` 的 `eslint` 在本 worktree 因无 `frontend/node_modules`
  而 not found（worktree 不共享依赖，属环境缺口、与本改动无关）；已在主检出 `npm --prefix
  frontend run lint` 验证通过。

## Revisit

- IDE 是否存在独立规则通道：下次 IDE 会话时补测（**版本号已补：4.11.3**）；
- IDE 根契约缺口：形态与 Zcode 同（根不注入），是否复用 Zcode 的「scoped 真身
  根指针」缓解即可，或需 IDE 专用供给，按实际影响评估；
- IDE 转正为可承接 Requirement 的 Harness：需 Registry CLI 全周期 dogfood
  实测（对照 dsh web #1256→PR #1291 先例）；GUI 无脚本通道是主要疑点。
