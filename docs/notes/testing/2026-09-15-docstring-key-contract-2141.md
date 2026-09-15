# docstring 键清单纳入响应形状对拍（同一形状的第三处声明）

Status: implemented
Class: testing

## Decision

把「docstring 里列出的返回键 vs 实现返回键」做成**自动发现 + 双向断言**，追加进
`tests/test_api_response_shape_contract.py`（`#2129` 那个文件的同一主题：同一形状的多处独立
声明会漂移）。`#2129` 覆盖 TS ↔ 后端两处；本单补上**第三处**——后端函数 docstring 的
`Returns a summary dict::` 块。它比 TS 更隐蔽：`tsc` 至少能看见 TS 侧，docstring 与实现不一致
则没有任何读者会被编译器拦住。

三件事：

1. **自动发现**：AST 扫 `backend/`，凡 docstring 里列了 ≥3 行 `"key": …` 的函数都进入检查。
   新出现的同类函数**无需登记**即被覆盖。
2. **双向断言**：docstring 键 ⊆ 实现返回键并集（无幽灵承诺）**且** 实现返回键并集 ⊆ docstring
   键（无未文档化）。
3. **豁免表 + canary**：发现到但**实现不以 dict 字面量返回**的函数无法比对，必须在
   `_DOC_UNCHECKABLE` 里显式豁免并写明原因；豁免项失效（不再被发现、或变得可比对）时红。
   另有 canary 断言 `abort_plan_run` 仍被发现——否则「docstring 块被删」会让这套检查
   **静默**失去最有价值的（`#2089` 漂移对的）那一例。

### 为什么这一侧可以用自动发现（与 `#2138` 相反）

`#2138` 明确否决了内容自动发现，靠的是实测：那里的内容信号漏掉四条已知必须覆盖的文件。
这里的信号是 AST 结构（docstring 里的键行），**实测可靠**：全仓 4 个函数，且不排除
`backend/agent/scripts`（随设备下发的脚本副本）也仍是 4 个 —— 所以连路径例外都不需要。
同理，本单没有靠「手工登记表」来定义范围，而是让范围随代码自动扩展。

实测现状（本单的前置事实）：4 个函数里 `abort_plan_run`(5 键) / `abort_jobs_for_host`(3 键)
有 dict 字面量返回且**当前与实现一致**；`from_job` / `process_device_logs` 描述的是非 dict
字面量返回的对象，无法比对 → 进豁免表。也就是说：本单**没有修任何现存漂移**，它是把一处
此前完全无兜底的声明面接上护栏；价值在「下一次漂移会被拦住」。

## Alternatives

- **手工登记表**（照 `#2138` 的做法）：否决。上一段已说明信号为何可靠；此处用登记表反而会让
  新函数逃过检查。
- **放宽 docstring 键行的正则**（允许 `\[int|"?` 等）：否决。实测同一批函数会从 4 个函数的
  「1 / 5 键」变成「6 / 10 键」——把非键行也吃进来 ⇒ 假红。用更严的
  `^\s*"key"\s*:` 并接受覆盖面略窄。
- **把两个不可比对的函数也强行比对**：否决。它们的返回不是 dict 字面量（`from_job` 返回
  `policy` 对象、`process_device_logs` 含无值 `return`），比对没有定义。显式豁免比猜测好。
- **排除 `backend/agent/scripts`**：实测不必要（加了也一样是 4 个函数），少一条路径例外。
- **缓存全仓 AST 扫描**：**采纳**（`lru_cache`）。5 个用例各扫一次 = 8.6s；仓库状态在用例集内
  不变，缓存后 2.07s。
- **指望评审/`tsc`/`knip` 兜住**：否决。这三者都看不见 docstring（`#787` 与 `#2089` 两次
  漂移也都是在**已有**前端检查之下发生的）。

## Verification

| 项 | 结果 |
|---|---|
| 契约测试（含新段落） | **8 passed in 1.83s**（缓存后；缓存前 8.59s） |
| 负向对照 A：docstring 加一个实现不返回的键 | 红：`abort_plan_run 的 docstring 承诺了实现不返回的键 ['ghost_probe']（幽灵承诺）` |
| 负向对照 B：docstring 删掉一个已实现的键 | 红：`abort_plan_run 实现了 docstring 未列的键 ['aborted_jobs']（未文档化）` |
| 负向对照 C：新加「docstring 列键但无 dict 返回」的函数且不豁免 | 红：`必须在 _DOC_UNCHECKABLE 里显式豁免并写明原因：[...zz_doc_probe.py:probe]` |
| 负向对照 D：删掉 `abort_plan_run` 的 docstring 键块（canary） | 红：`test_known_canary_is_still_discovered` |
| 恢复后复绿 / 根 `tests/` | 8 passed / **751 passed** |
| `ruff` / 治理面 / 差异面不变量 | All checks passed / `[OK]` / `[OK]` |

**一次自我更正（如实记录）**：负向对照 C 的第一版**没红**——我造的探针函数恰是「可比对」的，
按设计就该被自动覆盖、无需豁免。是我的对照设计错了，不是检查漏了；改成「无 dict 返回」的
探针后才如期变红。记录在此，因为它说明这四条对照是真的在执行判据，而不是在走过场。

## Revisit

- **发现的函数数量**：当前 4 个（2 可比对 / 2 豁免）。若可比对者涨到十几个，考虑把检查从
  「根 `tests/`（PR 路径、纯离线）」拆分为独立文件并按需并行；当前一次缓存扫描 1.5s，不必要。
- **豁免表的腐化**：`_DOC_UNCHECKABLE` 已由 `test_exemptions_are_not_stale` 守住（改成可比对
  即红）。若将来豁免项超过 3–4 个，应回头质疑判据本身是否选错了形状。
- **canary 的扩展**：目前只锚 `abort_plan_run`（`#2089` 漂移对）。若再出现「docstring 块被删
  导致静默失去覆盖」，把 canary 扩成「每个可比对函数」的最小数量断言。
- **第四处声明面**：若将来引入 OpenAPI 快照或由后端生成 TS 类型，这三处（TS / docstring /
  实现）应合并为**一处**声明 + 生成 —— 那时本文件可以整体退场。这与
  `docs/notes/testing/2026-09-15-response-shape-contract-2129.md` 的 Revisit 同源。
