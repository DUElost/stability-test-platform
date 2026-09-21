# 审计 action 动态登记锚点：行号 → 形态键（#3017 复核）

Status: implemented
Class: bug-fix
Related: #3017 / ADR-0049 D2 / PR #3025（原守卫）/ commit `3d8bb065`（PR #3031，触发方）

## Decision

先摆事实，因为它决定该不该动：在 main 尖端直接跑原守卫即 **1 failed / 3 passed**，
报的是 `services/plan_run_abort.py:781`、`:458` 「未登记动态 `action=` 调用点」。而这两处
的 `action=audit_action` 分别自 2026-05-07 / 2026-07-16 就存在——登记值写的是 `455` / `778`，
`3d8bb065`（#3031）在同一文件上方插入 3 行，把它们推成 `458` / `781`。也就是说 PR #3025
合入 22 分钟后，一次与本单毫无关系的合入就把两条**合法**登记判成了违规，而报错给出的处置
（「先把闭合 action 全集写入 allowlist 再登记」）指向的不是真实成因（行号漂移）。

1. 登记表 `_DYNAMIC_ACTION_SITES` 的键从 `(relpath, lineno)` 改为**形态键**
   `<相对 backend/ 的路径>::<所属函数链>::<action= 的源码>`，值改为**出现次数**
   （`dict[str, int]`）。同一函数里两处同形态调用（`abort_plan_run` 的两处
   `action=audit_action`）以 `: 2` 登记——出现第三处仍必须显式改表，不会自动放过。
2. 登记与实见**双向闭合**：实见 > 登记 ⇒ `unexpected_dynamic`（新调用点要人点名）；
   登记 > 实见 ⇒ 新增的 `zombie_dynamic`（调用点被改成字面量 / 换了所属函数 / 换了构造）。
   原实现只有「僵尸行」一条，且与漂移混在一起互相误判。
3. 键的构造只有一份实现（`_dynamic_action_key`）。`ast.walk` 不携带嵌套关系，
   故新增 `_iter_calls_with_scope` 自己带作用域链；测试夹具与生产扫描共用同一个键函数，
   不存在第二套判卷口径。
4. 补三条锚点用例，红绿两侧都落在夹具里：整段下移 7 行 ⇒ 键不变**且行号必须变**
   （行号没变就说明夹具是空转）；函数改名 / 换 `action=` 构造 ⇒ 键必须变；
   登记表每条都要能在真源码里取到（防登记与实现脱节重新隐形）。

## Alternatives

- **只把 455/778 改成 458/781**：否决。同一漂移会再次发生，且每次都要人现场判断
  「这是漂移还是新调用点」——报错文案本身就把人往错误方向引。属未标注的临时止血。
- **保留 lineno、改用「函数内第 N 次调用」作相对锚**：否决。函数内新增调用即整体错位，
  比形态键更脆，收益相同而自证更难。
- **动态构造一律免登记**：否决。静默面正是 #3017 要拦的东西。
- **顺手把 `backend/tests/` 拉进 PR 门禁**：否决（超范围）。见 Revisit 第一条，那是门禁
  预算决策，与本单的锚点选型无关。

## Verification

```
$ python -m pytest backend/tests/test_audit_action_retention_guard_3017.py -q
7 passed                                   # 改前在同一 main 尖端：1 failed, 3 passed
$ python -m pytest backend/tests/test_audit_read_side_alias_guard.py \
      backend/tests/test_audit_resource_type_guard.py -q
7 passed
$ python scripts/run_gates.py check:quick
[OK] check:quick (14 gates)
$ python -m ruff check backend/tests/test_audit_action_retention_guard_3017.py
All checks passed!
```

变异自证（都只动工作树，跑完 `git checkout --` 还原，`git status` 已确认干净）：

| 变异 | 期望 | 实测 |
|---|---|---|
| M1 `plan_run_abort.py` 顶部插 3 行（复现 #3031 的真实漂移） | 绿 | **7 passed**（改前正是这一形态把 main 弄红的） |
| M2 其中一处 `action=audit_action` → `action=f"{audit_action}"`（行号不变、形态变） | 红 | **2 failed**：一条「未登记形态」+ 一条「登记 2 处、实见 1 处」 |

## Revisit

- **为什么它红在 main 上却没人看见**：`backend/tests/` 不在任何 PR required check 里
  （`pr-agent-tests` 跑的是 `tests/` 与 `backend/agent/tests/`），全量 `backend-test`
  只在 UTC 18:00 的 `main-ci-backstop`。这条守卫从 13:45Z 起就在 main 上红。是否给
  「登记制守卫」单独一个便宜的 PR job，属门禁预算决策，另议。
- 本仓其它 `node.lineno` 只出现在违规**输出**里（瞬时值，不承重）；本次在 `tests/` 与
  `backend/tests/` 里未见第二张以行号为**期望值**的登记表。若将来出现同形需求，直接用形态键。
- `abort_plan_run` 两处同形态调用的计数目前为 2。删掉任一处都会走 `zombie_dynamic` 变红，
  这是有意的：计数即「该形态被人工确认过的份数」。
