# 门禁扫描面盲区收口：compileall / pollution 纳入根 tests/（#2535 同批）

Status: implemented
Class: process

## Decision

今天在 ruff 的门禁上发现「根 `tests/` 不在扫描集」（#2562 已补）。顺着做了一次
**门禁扫描面普查**：把每个 gate 的扫描面与仓库顶层目录逐一对照（含 `.py` 的顶层目录
只有 `backend/`、`tests/`、`tools/`、`scripts/`），结果还有两条漏了 `tests/`：

| gate | 旧扫描面 | 问题（实测） |
|---|---|---|
| `compileall` | `backend/ tools/ scripts/` | 往 `tests/` 放语法错误 → 旧命令**零命中**（`check:quick` 的语法检查对根 `tests/` 失明） |
| `pollution` | `find backend tools scripts frontend/src …` | 把空行率 49% 的文件放进 `tests/` → 旧命令**零命中** |

两处都补上 `tests`，本地（`scripts/run_gates.py`）与 CI（`ci.yml` 的
`pr-compileall` job 与 lint job 的污染检查 step）**四处同步**，保持 gate-parity。

**为什么 `tests/` 值得扫**：它是仓库里被改得最频繁的目录之一（今天一天内新增/改动
多个测试文件），而语法错误在本地 `check:quick` 里没有别的兜底（CI 侧靠 pytest 收集
才发现，但那是另一条路径、且要等 CI）；空行污染同理——`.githooks/pre-commit` 只拦
「单次提交新增行的空行占比」，对已污染文件无效。

**不纳入的**：`backend/agent/resources/`（第三方随包工具，`ruff.toml` 与污染检查
都已显式排除）、`backend/alembic/versions/`（历史 revision，#2258 不可改写）、
`backend/agent/scripts/*/v*/`（已发布版本，ADR-0020 不可修改）——对不可修改的代码
报问题无法修，只会制造噪声。

## Alternatives

- **只补 compileall（语法错误更严重）**：否决。两条 gate 的扫描面应当同源；
  只补一条会留下「为什么这条扫 tests、那条不扫」的下一个问题。
- **把 `tests/` 单独拆成一条 gate**：否决。同工具、同参数，拆开只会让「本地命令与
  CI 命令一致」更难维持（同 #2562 的取舍）。
- **顺带把 `docs/`、`deploy/` 也纳入**：不做。实测这两处没有 `.py`（`git ls-files`
  统计），纳入是空跑；将来若出现 Python 文件，再按同一模板补。

## Verification

- **反例构造（先证伪再采信）**：
  - 往 `tests/` 放 `def broken(:` → 旧命令零命中；**新命令 2 处命中**；
  - 往 `tests/` 放空行率 49% 的样本（函数体内单空行，`collapse` 会收敛）→
    旧命令零命中；**新命令 1 处命中**。两处探针均已清理。
- 实测命令与结果：
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (11 gates)**；
  - 四处命令与 CI step 的字符串逐字一致（`grep -n "compileall -q\|find backend tools scripts"`）。

## Revisit

- **普查应定期重跑**：本次是手工对照；若顶层目录将来增加（如新增 `sdk/`），应有人
  回答「它是否在每条 gate 的扫描面内」。可考虑把「gate 扫描面覆盖顶层 .py 目录」
  做成一条元门禁（对照 `run_gates.py` 的 GATES 命令与 `git ls-files` 的顶层目录），
  但那需要先定义「哪些 gate 该覆盖哪些面」的判据，留给后续裁决。
- **与 #2574 同文件**：两条改动都在 `scripts/run_gates.py` / `ci.yml`（不同区段）；
  谁后合入谁 rebase 即可。
