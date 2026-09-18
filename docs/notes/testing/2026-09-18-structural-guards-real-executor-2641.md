# CI 结构守卫改按「真实执行者」判：注释与陈旧 step 不再能满足判据（#2641）

Status: implemented
Class: testing

## Decision

#2445 修过这一类的一半——把 CI 锚点从「workflow 全文子串」改成「workflow 里某个
step 的 **name**」。另一半仍在：**判据落在 `run` 的原始文本上**，于是
①`#` **注释行**里的同形文本、②**已不再执行该命令的步骤**都能满足判据。

真实经过（#2641，tip `f1179f95`）：PR 路径的根 `tests/` 调用被搬进**并行子 shell**
后，`Run repo-level tests` 这个 step 只剩 `test -f repo.rc` 与两条
`# --ignore=…` 注释；把并行子 shell 里的**真实调用整段删掉**，三个结构守卫
（离线子集 / promtool 场景闸门 / lock-order PR 路径）**仍然全绿**——而它们正是
「PR 路径真跑根 tests/」「promtool 缺失即 fail」「容器测试不得混入 PR 路径」这三条
判据的唯一防线。

本轮把「真实执行者」的定义固定下来并让三个守卫共用：

1. 新增 `tests/ci_workflow_probe.py`：
   - `code_lines/ code_text`——剥掉空行与 `#` 注释行，只留真会被 shell 执行的行；
   - `steps_running(needle, only_pr=…)`——`run` 的**代码**里含 needle 的 `(job, step)`；
   - `is_pr_reachable(job)`——PR 可达性（无 `if` 或 `== 'pull_request'`）。
2. `tests/test_offline_subset_guard.py`：`--ignore=` 名单改为**只从真实执行者的代码行**
   提取（旧实现是整文件正则，注释里的同形文本即可满足）；并在 `run_gates.py` 侧
   同口径比对；新增合成红绿自证（注释不算 / 真实调用算）。
3. `tests/test_ci_promtool_scenario_gate.py`：`test_consumer_step_runs_root_tests`
   改成断言**代码行**里有 `python -m pytest tests/`（旧断言 `"tests/" in run` 可被注释满足）。
4. `tests/test_lock_order_pr_path_contract.py`：挑选候选步骤时同样只看代码行。
5. **`.github/workflows/ci.yml`：删掉那两条「结构守卫扫描锚点…勿删」注释**——
   它们的存在理由是让旧判据通过，如今判据不再读注释，留着只会误导下一个人
   （注释变成了「为了让门禁绿而存在」的诱饵）。

## Alternatives

- **保留注释锚点、只把守卫改成「注释里也要有」的双重断言**：否决。那等于把
  「注释必须与真实调用同步」变成一条需要人肉维护的不变量——而本轮要消灭的正是
  这种「靠文本巧合维持的绿」。
- **把真实调用搬回 `Run repo-level tests` step、恢复「一步一命令」**：否决。
  并行子 shell 是**有意的墙钟优化**（agent 套件与根 tests/ 同 runner 并行，
  #2495 方向），守卫应当跟上现实，而不是让现实迁就守卫。
- **给 `run` 块加「禁止注释」之类的写法约定**：否决。约定不可执行；代码行剥离
  是机械且可自证的判据（合成用例已钉住）。
- **一次性把所有读 `run` 的守卫都改掉**：本轮只改被 #2641 点名的三条（加共享探针）。
  其余（见 Revisit）登记为同类可收紧项——避免把一次修缺陷变成大范围重构。

## Verification

- **变异 1（#2641 的原始变异）**：删掉 PR 路径并行子 shell 里的真实 pytest 调用与两个
  `--ignore` 实参（保留 step 名与注释）→
  `test_offline_subset_guard` 的 **3 条 FAILED**（`no_container_file_misses…` /
  `run_gates_matches…` / `ignore_lists_are_not_vacuous`）；而 issue 记录的旧形态是
  **25 passed**。
- **变异 2**：把夜间 `Run repo-level tests` 的真实调用换成注释（保留 step 名与
  `PROMTOOL_REQUIRED`）→ `test_consumer_step_runs_root_tests` **FAILED**。
- 两处变异恢复后：三条守卫 **34 passed**；`tests/` 全量 **1495 passed, 0 failed**；
  `ruff check tests/` 全绿；`check:quick` **[OK] (12 gates)**。

## Revisit

- **仍按原始 `run` 文本判的守卫**（同类可收紧项，本轮未动）：
  `tests/test_main_ci_backstop_guards.py:36`、
  `tests/test_backstop_attribution.py:195`、
  `tests/test_ci_promtool_scenario_gate.py::test_install_step_verifies_digest_and_self_proves_version`
  （`sha256sum --check` 等命令判据）、
  `tests/test_lock_order_pr_path_contract.py:177`。新增判据请优先用
  `tests/ci_workflow_probe.py`，不要再各写一份文本扫描。
- **`ci_workflow_probe` 的边界**：它只回答「哪一步真会执行这段命令」，不回答
  「命令本身是否正确」。后者仍归各守卫的具体断言。
- **真实执行者唯一性**：离线子集守卫现在依赖「PR 路径恰有一个跑 `python -m pytest
  tests/` 的步骤」——将来若拆成两个（例如按 marker 分片），判据要改成「全部执行者的
  并集」，别只取第一个。
