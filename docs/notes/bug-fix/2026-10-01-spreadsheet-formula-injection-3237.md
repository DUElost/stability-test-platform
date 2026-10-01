# Spreadsheet 公式注入形态收口（#3237，批次 B3 / #3561 G1）

Status: implemented
Class: bug-fix

## Decision

按批次方案 [#3561 v1.1](https://github.com/DUElost/stability-test-platform/issues/3561) 的
§1.6 最小修法 contract 实施，**不重新设计**。方案 v1.1 是本单元唯一实施规范。

新增一个窄纯函数模块 `frontend/src/utils/spreadsheet.ts`，只承载 spreadsheet cell 安全语义，
不是 export framework：

- `neutralizeSpreadsheetCellText(value: string): string` —— 首字符命中 B3 触发集合
  （`= + - @ TAB CR LF NUL` 与全角 `＝ ＋ － ＠`）时前置 `'`；**只接受 string**。
- `escapeSpreadsheetClipboardCell(value: string): string` —— 先把内嵌 `TAB/CR/LF/NUL`
  转成两字符可见字面，再做公式中和，最后对行首 `"` 补 apostrophe。

触发集取 OWASP WSTG Latest（WSTG-INJT-21）与 ASVS 5.0 V1.2.10 的并集；
全角项是环境相关的保守覆盖，不宣称所有 locale 都会执行它们。

分层原则：**framing 与中和互不替代**。三个 CSV sink 全部改成「先对逻辑字符串值做中和，
再执行各自现有的 quote/double-quote 规则」；S1/S2/S3 的既有 framing 行为一律不回退。

涉及调用点（S1–S5，全部在 §3 scope 内）：

| ID | 文件 | 改动 |
|---|---|---|
| S1 | `frontend/src/pages/devices/DevicesPage.tsx` | `handleExportSelected` 的 `csvCell` 对原始 string 调中和；tags 在 `join('|')` 后**整格判定一次**，不逐 tag 改底层数据 |
| S2 | `frontend/src/pages/execution/PlanRunLogsPage.tsx` | `csvCell` 同上；所有字符串格统一中和，不按字段来源可信度豁免 |
| S3 | `frontend/src/components/execution/plan-execute/planExecuteExport.ts` | `csvEscape` 在既有 escaping 前先中和 |
| S4 | `frontend/src/pages/devices/DevicesPage.tsx` | `handleCopySerials` 每条 serial 过 clipboard helper |
| S5 | `frontend/src/components/execution/plan-execute/planExecuteExport.ts` | `formatSerialsClipboard` 每条 serial 过 clipboard helper |

S5 的消费方 `PlanExecutePage.tsx` 只调用 `formatSerialsClipboard`，无需改动，因此没有
超出声明 scope 的文件变更。

**类型契约是本单元最容易做错的一点**：调用方必须在 `String(...)` 强制转换**之前**判断
原始类型。若先 `String()` 再中和，number `-5` 会变成文本 `'-5`，把导出值直接废掉。
`csvCell` 因此显式分 `typeof value === 'string'` 与 number/null 两路。

CSV 的 apostrophe 可见性是**已接受的输出契约**：危险值中和后在 raw CSV 里以可见单引号
开头（`'=1+1`）。这会影响拿导出值直接做 VLOOKUP / 等值回查的场景；本批接受该取舍，因为
只改变导出/复制表示，不改 STP 内部原始数据。

## Alternatives

- **把中和塞进现有 `csvEscape`/`csvCell` 各自的实现里，不建共享 helper**：三处要复制同一份
  触发集与同一份类型分支，日后改触发集必然漏改一处。放弃。
- **抽一个通用 export/download/Blob 框架**：超出 Owner Appetite，且会把 MIME、Blob、下载器
  一起收进共享层。方案 §6 明确不做。
- **按字段来源的可信度跳过某些列**（例如认为 S2 的 `title` 多来自服务端动作名）：方案
  §1.3 / §8.1 已裁决不这么做——来源追溯只用于证明影响面，不构成「这个字段可以跳过 helper」
  的安全条件。
- **引入 XLSX 或二进制导出**：save→reopen durability 明确在 B3 承诺外，需要时应退回 Planner
  重新判断，不由实施者扩大目标。
- **全角字符不做覆盖**：省掉四条反例，但把安全性押在客户端 locale 上。采纳覆盖而非豁免。

## Verification

自动化（全部实际运行，见 PR 正文逐条命令与结果）：

- 定向 vitest：`spreadsheet.test.ts`、`DevicesPage.test.tsx`、`PlanRunLogsPage.test.tsx`、
  `planExecuteExport.test.ts` 共 68 passed。
- `cd frontend && npx vitest run`：138 files / 1216 tests passed。
- `npm run type-check`、`npm run lint -- --max-warnings 0`、`npm run build` 均通过。
- `python scripts/run_gates.py check:quick`：16 gates OK。
- `python scripts/run_pytest.py tests/ -q`：2094 passed / 18 skipped。

**判别力变异（§4.3）**，全部 RED 后还原：

- 触发集逐字符负向变异 12 项（移除 `=`/`+`/`-`/`@`/`TAB`/`CR`/`LF`/`NUL`/四个全角），
  **每一项都让套件变红**，无存活项。
- S1–S5 逐 sink 绕过中和调用，五个对应测试文件全部变红。
- 行首 `"` 结构触发字符从 helper 中移除 → 4 处测试变红（helper + S5 + S4），
  该反例进入了调用点变异而不只测 helper。
- 「先 `String()` 再中和」的错误实现在 S1、S2 上分别被抓住（S1 需要一个 **负数 id**
  才能判别：正数首字符不在触发集里，用正数 id 的测试抓不住这个 bug）。

**真实客户端（§4.4）**：本机有 LibreOffice Calc，已实测；**Excel 与 WPS 未验证**
（环境不存在，未以推测代替结果）。

- LibreOffice Calc 打开 CSV：`=1+1` → `2`（被求值）；`"=1+1"` → `2`（**外层双引号同样被求值**，
  实证 framing 不等于中和）；`'=1+1` → 原样保留（中和后未被执行）；`+1+1` / `@SUM(1,1)` /
  `＝1+1` 在该 locale 下未被执行（保守覆盖无害）。
- clipboard 解析（`--infilter` 强制 Calc + TAB 分隔）：修复前 3 条 serial 被解析成
  **4 行且带多余空列**（裸 TAB/LF 破坏了单元格边界）；修复后为 **3 行、无多余空列**，
  一条 serial = 一个 cell 成立。

## Revisit

- **save→reopen durability 不在 B3 承诺内**：若业务要求「Excel 另存为 CSV 后再次打开仍安全」，
  需退回 Planner 重新判断是否引入 XLSX / 新导出模型，不要在本模块内扩范围。
- **全角触发字符是保守覆盖**：若后续拿到多 locale 的真实客户端证据显示全角永不被执行，
  可评估收窄触发集；在拿到证据前不要按「大概是冗余」删掉。
- **Appetite 外残余**：仓外 `/tool/generate_transsion_jira_upload_list.py` 源码不可得，
  只记为未核实残余。若日后确认其确有 spreadsheet formula injection，按 ADR-0058 D5
  升级 Owner，不得顺手并入本单元。
- 本单元为 security 形态，合入不等于生效：实际部署按 #3561 §5 在激活阶段完成。
