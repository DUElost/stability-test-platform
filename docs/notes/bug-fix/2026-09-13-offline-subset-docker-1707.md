# #1707 修 PR 路径离线子集混入容器测试 + 补结构守卫

Status: implemented
Class: bug-fix

## Decision

两处改动收口 #1707：

1. **补齐排除名单**：`ci.yml`（`pr-agent-tests` → `Run repo-level tests`）与
   `scripts/run_gates.py`（`repo-tests`）的 `--ignore` 追加
   `tests/test_script_seed_governance.py`；同步更正 `ci.yml` 中「除
   test_alembic_upgrade.py 外全部纯离线」的**错误判据注释**。
2. **补结构守卫** `tests/test_offline_subset_guard.py`（5 例）：扫 `tests/*.py`，
   凡**实际构造** testcontainer 的文件必须出现在两份 `--ignore` 名单中，且两份名单
   必须互相一致。

## 缺陷确认（#1569 遗留，我自己的判据错误）

#1569 的注释断言「`tests/` 除 `test_alembic_upgrade.py` 外全部纯离线」。
**该断言与事实不符**：`tests/test_script_seed_governance.py` 在模块级 fixture
（`:25-27`）里 `with PostgresContainer("postgres:16")`，同样真实起容器。

**漏判原因**：#1569 当时按**镜像名**排查（`grep -l "postgres:16"` 之类），而该文件
用的是 `PostgresContainer(...)` **构造器**（镜像名只是入参），形态不同故未命中。

**后果（潜在，非当前故障）**：required check 从此依赖 docker 与镜像拉取。实测
`DOCKER_HOST=tcp://127.0.0.1:1`（模拟无 docker）：

```
ERROR tests/test_script_seed_governance.py::test_unreferenced_version_passes
ERROR ...::test_referenced_version_aborts_with_guidance
ERROR ...::test_batch_deactivate_aborts_listing_all_blocked
3 errors, exit 1
```

即 runner 无 docker 时 `pr-agent-tests` 整体红、**阻塞全部合入**，同 job 后续步骤
skipped。**当前 GitHub runner 有 docker，故未暴露**——本次实测确认：CI 日志
`235 passed, 1 skipped`，含该文件的 3 例（本地对照 236 vs 排除后 233，差 3 恰为该
文件用例数），证明容器确实在 CI 中成功启动。

结论：**当前是潜在风险而非活跃故障**，但「required check 依赖 docker」本身与
#1569 立下的「纯离线 + 秒级」判据矛盾，故仍须收口。

## 判据取「实际使用」而非「import」

守卫的正则匹配 `PostgresContainer\s*\(`，而非 `import testcontainers`——后者会误报
仅**提及**该词的文件：`tests/test_ci_test_db_guard_wiring.py`（注释里说明「不触发
testcontainers」）、`tests/test_requirements_lock.py`（参数化列表里含包名
`"testcontainers"`）。已用
`test_mentioned_but_unused_files_are_not_flagged` 钉住这一区分。

## Alternatives

- **给 `test_script_seed_governance.py` 的 fixture 加「docker 不可用即 skip」守卫**
  → 否决（本单范围）：skip 会让该文件在无 docker 时**静默不跑**，而它是种子迁移
  治理（#942）的真实 PG 行为测试；`--ignore` 保持「在夜间 backend-test 真实执行」
  的语义（那条路径有 PG service），不制造静默空跑。
- **保持忽略名单，不加结构守卫** → 否决：这正是 #1707 的成因——名单是手工维护的，
  而 #1569 的设计是「新增文件默认进 PR 路径」（黑名单式 `--ignore` 的固有代价）。
  没有守卫时，下一个容器测试会以完全相同的方式静默混入。
- **改为白名单（显式列出离线文件）** → 否决：与 #1569 已确立的「新文件默认受覆盖」
  相反——白名单下新文件默认**不受**覆盖，会把缺口方向翻转成更糟的形态。
- **给 `PostgresContainer` 打桩/改用 testcontainers 的 ryuk 探测** → 否决：
  侵入被测行为，且把「是否需要 docker」这一静态事实变成运行时推断，脆弱。

## Verification

- `python -m pytest tests/test_offline_subset_guard.py -q` → **5 passed**（0.02s，纯离线）；
- **缺陷复现**：`DOCKER_HOST=tcp://127.0.0.1:1` 跑 `test_script_seed_governance.py`
  → **3 errors, exit 1**（无 docker 硬失败）；
- **修复后无 docker 实测**：同环境跑完整离线子集
  → **235 passed in 4.95s**（此前该命令会因容器文件红）；
- **红绿双向（守卫有效性）**：
  ① 临时新增 `tests/test_zzz_new_container.py`（真实构造容器）→ 守卫**红灯**且点名
     该文件；移除后 → 5 passed；
  ② 从 `run_gates.py` 删掉一条 `--ignore`（模拟口径漂移）→
     `test_run_gates_matches_ci_ignore_list` **红灯**；还原 → 5 passed；
- **自指误报已修**：守卫自身正则字面量含 `PostgresContainer(`，首轮被自己命中
  （实测暴露）；已显式排除本文件，并保留 `test_detects_known_container_files`
  自证扫描器非空转；
- **CI 事实核验**：`pr-agent-tests` 日志 `235 passed, 1 skipped`；本地对照组
  236（含 seed）vs 233（不含 seed）差 3 = 该文件用例数 → 证明容器在 CI 中成功启动，
  本缺陷当前为潜在风险；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿；
- `ruff check tests/test_offline_subset_guard.py` → All checks passed；
- `ci.yml` YAML 解析通过。

## Revisit

- **守卫的判据仍是正则**：若 `tests/` 出现以其他方式起容器（如 `docker compose`、
  `subprocess` 调 `docker run`、或把构造器重命名/间接封装），本正则不覆盖。
  出现第二类形态时应扩展为「扫描 docker 交互面」而非继续堆正则。
- **推而广之**：`scripts/run_gates.py` 的其他 gate 若也声明「纯离线」，同样需要
  这类结构断言；本单只覆盖 `repo-tests` 一处。
- **#1707 的「GitHub runner 无 docker 是否现实」**：本次确认当前 runner **有**
  docker（CI 中容器测试成功），故本单修的是**判据与事实的矛盾**与**依赖面暴露**，
  而非正在发生的故障。若 GitHub 变更 runner 能力，弹窗会立即变为活跃故障——
  守卫此时的价值是让这类变更在 PR 上可见，而非等 CI 整体变红才发现。
