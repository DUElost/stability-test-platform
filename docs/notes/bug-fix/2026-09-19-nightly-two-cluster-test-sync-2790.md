# 夜间全量两簇红灯修复：快照调用点与 connect_args 断言跟上实现（#2790）

Status: implemented
Class: bug-fix

## Decision

**归因：两次生产代码合入改了接缝，既有断言未同步；`backend-test` 只在夜间 backstop
跑，PR 路径零拦截**——nightly run 35390316405（2026-09-18T20:13Z）实红 12 条，两簇：

1. **`6e2b1a7c`（ADR-0048）删除 `build_plan_snapshot` 第 4 参 `failure_threshold`**，
   `test_plan_barrier_timeout.py` 仍有 10 处按 4 参调用 → `TypeError`（10 failed）。
   该 commit 已把 `_plan()` 默认参里的 `failure_threshold` 删掉，漏的只是位置实参。
2. **`2027d21d`（#2632 缺口①）给 async/sync engine kwargs 加 `connect_args`**，
   `test_database_config.py` 两条**精确 dict 相等**断言未同步（2 failed）。

**修法是让用例跟上实现，不是回退实现**：ADR-0048 是有意收窄判定轴（通过率判定退役），
`connect_args.application_name` 是有意的 PG 日志归因手段，两者都正确。

具体改动：

- `test_plan_barrier_timeout.py`：10 处调用去掉第 4 位置实参（语义在 ADR-0048 下已不存在）；
- `test_database_config.py`：两条 dict 断言补 `connect_args`——
  async 形态 `{"server_settings": {"application_name": "stability-tests"}}`（asyncpg 走
  `server_settings`，与 psycopg 不同的实测坑），sync 形态顶层 `application_name`。
  名字映射（TESTING=1 → tests 名）不在本文件重复钉，由
  `backend/tests/core/test_db_application_name.py` 承担。

**不新增防复发守卫**：ADR-0048 的提交已带 AST 结构钉；本单两簇属「签名/契约变更未扫
调用点」，机械同步即可，再加守卫是重复投入。**不动 CI 拓扑**：PR 路径不含全量后端测试
是 §6 的有意取舍（~2min 注意力预算），是否前移属独立决策（见 Revisit）。

## Alternatives

- **A. 只改簇 1，簇 2 留待后续**：否决。两簇都是同一类「接缝改了用例没跟」，且都在同一次
  nightly 里实红；只修一半会让 #2628 的 backstop 仍然红。
- **B. `test_database_config.py` 改用 `db_application_name()` 参与期望值**：可行但欠明确
  ——断言里读不到「线上的期望值长什么样」，且与 `test_db_application_name.py` 的职责重叠；
  改钉字面量（本文件即精确相等风格）。
- **C. 断言改为「池参数子集相等」**（#2790 建议的另一写法）：放宽后 connect_args 被删不会
  变红；本文件现有风格是精确 dict，补齐比放宽更符合原意。
- **D. 把后端全量测试前移到 PR 路径**：否决（本单范围内）。会显著拉长每次 PR 的反馈时间，
  属仓库级时间预算决策，须独立裁决。

## Verification

- **红（修复前，本机 tip 复现）**：
  `.venv/bin/python -m pytest backend/tests/services/test_plan_barrier_timeout.py backend/tests/test_database_config.py -q`
  → **12 failed, 37 passed**（与 nightly 两簇一致）；簇 1 报
  `TypeError: build_plan_snapshot() takes 3 positional arguments but 4 were given`。
- **绿（修复后）**：同命令 → **49 passed**；另跑受影响集
  （`test_plan_barrier_timeout.py` + `test_plan_dispatcher.py` + `test_db_application_name.py`）
  → **75 passed**。
- **AST 扫描**：全仓（`backend/**/*.py`）无 >3 位置实参的 `build_plan_snapshot` 调用。
- `python scripts/run_gates.py check:quick`：见 PR 描述。

## Revisit

- **PR 路径零拦截是同一根因**（#2628 的归因段）：后端全量只在夜间 backstop 跑，接缝类
  回归平均延迟一天暴露。若要收敛，应与前端 vitest 前移（#2027 Revisit）一并评估。
- **#2628 自动关**：本修复合入后下一次 nightly 全绿会关掉 backstop 单；若仍红，按新失败
  簇另立单，不回到本单。
