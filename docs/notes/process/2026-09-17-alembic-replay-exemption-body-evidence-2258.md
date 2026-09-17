# alembic 不可变门禁：豁免必须有 body 证据（#2258 重开后的残余收口）

Status: implemented
Class: process

## Decision

重开说明把范围收得很窄，本单只修那一条：**发放豁免的判据从「有一条新增 revision 的
`down_revision` 指向被改写者」收紧为「该新增文件的 `upgrade()` 还有实际语句」。**

实现上拆成三层，让判据本身可离线自证：

| 层 | 函数 | 为什么放这里 |
|---|---|---|
| 形态判定 | `upgrade_body_is_effective(text)`（AST） | 排除 `pass` / 裸 docstring / `...`；**解析失败按 False**（fail-closed：读不懂就不放行） |
| 纯判据 | `replay_exemptions(added_texts)` → `(可豁免, 因空 body 被拒)` | 不碰 git，self-test 能直接喂源码字符串做红绿双向 |
| 取源码 | `_exempt_from_additions(base, added)` | 唯一与 git 交互的一层 |

留痕一并改口径：豁免成立时打印**「按形态豁免：`down_revision` 指向被改写者 + `upgrade()` 非空；
body 是否真是幂等自愈的『命中才写』未验证，仍需人看」**；因空 body 被拒时另打一条
`不据 X 发放豁免：… 空 body 不是重放证据`。**不假装验了语义** —— 机器能查的只有形态，
把这条边界写明，比默默放行或默默过严都更有用。

为什么"正常续链新增一版"这一半仍没被机器排除：仅凭结构无法区分「为重放而新增」与
「在 head 后正常新增」（两者 `down_revision` 都指向被改写者）。要区分得引入显式登记
（如 `REPLAY_EXEMPTIONS` 清单），那是另一个决策 —— 现在的非空 body 判据已经把
"随手加一版骗过门禁"这条路堵掉，剩下的形态由留痕自述兜住。登记清单记在 Revisit。

## Alternatives

- **维持旧判据，只在 NOTICE 里加一句"未验证"**：否决。重开理由正是"空 body 的假重放
  也放行"，只加措辞不改判据，等于把侧门重新描述一遍。
- **要求 body 命中特定写法（`INSERT ... WHERE NOT EXISTS` / `ON CONFLICT DO NOTHING` 等正则）**：
  否决。命中式写法只是先例 #1717 的形态之一，把它写成唯一合法形态会误伤合法重放
  （例如只做 `UPDATE ... WHERE` 或调服务函数），且正则一旦成为判据，人就会开始
  为过门禁而凑字串 —— 那正是"形态证据"被误当成"语义证据"的下场。
- **加一份显式登记清单**（`revision → 重放文件`）：本轮不做。它把判断搬进一次人工登记，
  需要与"谁有权登记、登记错了怎么撤"一起设计；现在这条判据收紧已能消掉现场问题。
- **让门禁去读 base 与 HEAD 两侧 body 做语义对比**：否决，等价于要求工具理解迁移逻辑，
  超出"不可变 + 附重放"这一契约的机械范围。

## Verification

- 工具自证 `--self-test`：新增 4 条，**空 body 假重放 → 不豁免（红）**、**只有 docstring
  的 pass body → 不豁免（红）**、真重放（非空 body）→ 豁免（绿）、源码解析失败 →
  fail-closed（绿）；全 15 项 OK。
- 行为测试 `tests/test_alembic_revision_immutability_gate.py` **7 passed**（在临时 git 仓
  真实构造历史，不是 grep 工具源码）：
  - `test_gate_allows_rewrite_with_effective_replay`：放行且留痕含"未验证"；
  - 新增 `test_gate_blocks_empty_body_fake_replay`：改写 + 空 body 新增 → **红**，且输出
    点名"空 body 不是重放证据"（这条正是重开说明要求的红样例）。
  - 既有 5 条（删除/改名一律红、三点 diff 只看本分支、非 versions 路径不参与、
    正常新增放行、git 失败不静默绿）一字未改，仍绿。
  - **注意**：原先那条"附重放迁移 → 绿"的用例用的是**空 body 模板**（重开理由里点名的
    同一件事），已改为非空 body 版本；也就是说，**旧测试文件本身就在验证一个不该放行
    的形态**——这轮把测试与实现一起纠正，而不是只改实现。
- **红绿自证**：只回退工具、保留测试 → **2 failed**（两条与新判据相关的用例红）；
  恢复后 7 passed。
- 门禁侧保留项复核：删除/改名一律红（`classify_change` 里"豁免只对 M 成立"未动）。
- `check:quick` / `check:pr`（含 `alembic-immutability` gate）、`ruff`：见 PR。

## Revisit

- **显式登记清单**：若再出现"改写已合入 revision + 正常续链"被误放行的实例，就引入
  `REPLAY_EXEMPTIONS: dict[被改写 id, 重放文件]`，并要求豁免必须命中登记；同时定义
  登记的准入与撤销（谁能写、写错怎么撤）。
- 本门禁仍**只对当次 diff 生效、无回溯能力**（模块 docstring 已记两例历史）。存量清单
  随事件增长，后来者读到的不该是"只发生过一次"。
- 若要更严：可比对 base 与 HEAD 两侧 body，把"改写只动了 `down_revision`"（纯 rechain）
  与"改写动了函数体"（#2055 那类）分开对待 —— 后者才真正需要重放。
