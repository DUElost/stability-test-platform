# 源扫描守卫第八批：最后 9 处迁移完成，家族按「残余已分类」收口而非清零（#2639 第八批）

Status: implemented
Class: testing

- 日期：2026-09-20
- 关联：`#2639`（本族立单，已 CLOSED；无单推进）、第六批（三类不可照迁形态）
  `docs/notes/testing/2026-09-20-source-scan-batch6-three-classes-2639.md`、第七批（循环形态锚点表）
  `docs/notes/testing/2026-09-20-source-scan-batch7-deploy-ci-2639.md`
- 落点：`backend/tests/services/test_agent_installer.py:1`、`backend/tests/services/test_dedup_scan_merge.py:1`、
  `backend/tests/services/test_job_log_signal.py:1`、`tests/test_dev_bootstrap_seed.py:1`、
  `tests/test_pg_restore_drill.py:1`、`tests/test_script_seed_static_guards.py:1`、
  `tests/test_seed_revision_version_guard.py:1`、`tests/test_source_scan_anchor_ratchet.py:1`
- 堆叠关系：基于第七批分支（`test/2639-source-scan-batch6` → `test/2639-source-scan-batch7`）之上，
  须排在其后合入；**本批是常规迁移的最后一批**

## Decision

**迁移 9 处 / 7 文件**，锚点仍一律编在「取代被禁形态的那一样东西」上：

| 位点 | 锚点（替代物） | 禁词 |
|---|---|---|
| `test_agent_installer.py:281`、`:282` | 临时 inventory 里的 `ansible_ssh_private_key_file=<key_path>` | `ansible_password=`、`ansible_become_password=`（#1252 仅私钥凭据不落空密码键） |
| `test_agent_installer.py:313` | 临时 inventory 里的 `ansible_password=secret` | `ansible_ssh_private_key_file`（无 key_path 却写出私钥键） |
| `test_dedup_scan_merge.py:1274` | `def _center_event_dir_from_dle`（`expect=1`） | `Path(center_root, "devices").glob`（跨 run glob，#2888 契约 ③） |
| `test_job_log_signal.py:89` | `from agent.aee.paths import resolve_shared_storage_root` | `for alias in ("STP_WATCHER_NFS_BASE_DIR"`（复制第二份解析，#213 E2） |
| `test_dev_bootstrap_seed.py:193` | `print("dev_db_schema_ready path=alembic")` | 裸 `print("dev_db_schema_ready")`（#2381 两条路径在日志里同形） |
| `test_pg_restore_drill.py:103` | `if [ "${TABLE_COUNT}" -lt 5 ]; then` | `WARNING`（表数不足必须是 fatal） |
| `test_script_seed_static_guards.py:309` | `--ignore=tests/test_script_seed_governance.py`（两个接线文件**共同**的合法 ignore 行） | `tests/test_script_seed_static_guards.py`（本文件被 PR 路径 ignore＝静态判据白拆） |
| `test_seed_revision_version_guard.py:228` | 补行末位 `CAST(:pschema AS jsonb), CAST(:dparams AS jsonb), false,` | `is_active = true`（修复不得扩大派发面，#2399） |

`BASELINE` **12 文件/20 处 → 5 文件/11 处**；`SITE_FLOOR` 保持 10（不动，理由见 Alternatives）。

**本批新增一类形态：运行期产物、但轴二认不出来**（`test_agent_installer.py` 3 处）。它读的
是 `tempfile.mkstemp` 生成的 inventory，可路径是从被测函数的**返回值**里取的
（`Path(out["cmd"][out["cmd"].index("-i") + 1])`），AST 上认不出「根在临时目录」，所以仍是 offender。
处置＝**不扩判据、照常迁移**：用 `SourceGuard(text, origin=…)` 直接对产物文本建守卫，锚点编在
同一条产物里必然存在的凭据键上。这样既不需要放过任何位点，又把「产物为空 ⇒ 否定断言恒真」
这条唯一的静默路径变成 `AnchorDrift`。

**家族出口**：剩下 11 处全部属于**已逐条论证过不该照第四批编法迁**的三类残余——
A 类·不可变已发布脚本版本 7 处（退役时 `read_text()` 抛异常＝大声失败，连空守风险都没有）、
i 类·预处理本身有功能 1 处（判的是去注释后的视图，迁到原文视图＝改契约）、
循环多位点 3 处（`test_deploy_scripts.py`，四个脚本各无共同替代物，需 per-file 锚点表）。
所以**合格线不是清零**：常规迁移面已到尽头，继续做只会造出假锚点或往危险方向（放过）加规则。

## Alternatives

- **把轴二扩成「路径来自被测函数返回值也算产物」**（否决）：这是第 4 次往放过方向加规则，
  而放过方向的假阴性静默；且无法用夹具钉边界——返回值完全可能是仓库内路径。
  照常迁移同样能消掉空守风险，代价小得多。
- **`SITE_FLOOR` 上调到 11 贴住残余**（否决）：贴现状就违背地板的定位——它只在判据被**大面积**
  削弱时响。残余里 A 类 7 处会随脚本版本退役自然消失，若地板贴着 11，退役一次就要同时改两处。
- **把 A 类升格为判据级放过（口径轴三：路径根在 `backend/agent/scripts/*/v*/` ⇒ 不算漂移面）**
  （仍否决，第六批理由不变）：为 7 处永久残余新增一条放过规则，收益低于风险。
- **删掉 `SITE_FLOOR`，只留 `BASELINE` 双向收紧**（否决）：`BASELINE` 只防「新增/该删不删」，
  防不了「判据本身被改弱到几乎不命中」——那正是本族要防的病。

## Verification

- 9 个文件（棘轮 + 本批 7 个目标 + `#2949` 的跨包导入守卫）：
  `env -u DATABASE_URL <venv>/python -m pytest tests/test_source_scan_anchor_ratchet.py tests/test_cross_package_import_symbols.py tests/test_dev_bootstrap_seed.py tests/test_pg_restore_drill.py tests/test_script_seed_static_guards.py tests/test_seed_revision_version_guard.py backend/tests/services/test_agent_installer.py backend/tests/services/test_dedup_scan_merge.py backend/tests/services/test_job_log_signal.py -q`
  → **170 passed**（跨包守卫 5 例在场，证明本批新增的 `backend/tests → tools.dev` 导入不与 #2949 打架）
- 棘轮双向：`test_offenders_equal_baseline_no_growth_no_staleness` 绿 ⇒ 现状 offender 集合 == 新 `BASELINE`
- 剩余存量实测 **5 文件 / 11 处**，与 `BASELINE` 逐文件注释之和相等
- **变异 13 条，逐条红在正确的因上**，全部文件字节还原（`restored-identical: True`），
  还原后复跑基线 rc=0。**最终基线（rebase 到含 #2956 的 `origin/main`）再跑一遍，仍 13/13 PASS**：
  1. 服务真写回空 `ansible_password=` → `FormRegression`
  2. inventory 锚点改错 → `AnchorDrift`
  3. 密码路径偷写私钥键 → `FormRegression`
  4. `dedup_scan.py` 塞回中心盘 glob → `FormRegression`
  5. dedup 锚点改错 → `AnchorDrift`
  6. 忘下调 `BASELINE`（把已迁文件加回去）→ staleness 点名它——**证明迁移是真的**
  7. `run_gates.py` 把本文件 ignore 掉 → `FormRegression`，报错点名 `run_gates.py`（循环两位点各自可判）
  8. 去掉 `.anchored()` → `GuardMisuse`（空守）
  9. 修复迁移出现 `is_active = true` → `FormRegression`
  10. `init_dev_db.py` 退回裸 ready 行 → `FormRegression`
  11. `pg_restore_test.sh` 出现 `WARNING` → `FormRegression`
  12. `upload_manager.py` 塞回 alias 探测循环 → `FormRegression`
  13. 把 pg_restore 那条退回裸 `assert "WARNING" not in …` → growth 点名 `test_pg_restore_drill.py`
  （变异跑法：一次性脚本按「快照原字节 → 单条落点变异 → 只跑对应测试 → 还原」循环，收尾复跑基线）
- 门禁：`scripts/run_gates.py check:quick` → **12 门通过**；`scripts/run_gates.py check:pr` → **21 门通过**；
  `ruff check tests backend/scripts backend/services backend/agent scripts tools` → All checks passed
- 根 `tests/` 全量（rebase 到含 #2956 的 main 之后）：`env -u DATABASE_URL <venv>/python -m pytest tests/ -q`
  → **1704 passed**，与第七批分支同数＝本批只改写既有用例，未增删用例
- `backend/tests/ --collect-only -q` → **3434 collected**，无收集错误（后端整目录全量属夜间面，未跑）
- `tools/dev/check_governance_surface.py --check` → 阻塞项全绿（S1–S15、S5x）
- pending：合并后 detached 复跑（本批全部数字均取自「已含 #2944/#2956 的 main 之上」的工作树）

## Revisit

- **家族收尾后只剩两条轴，都不属于「迁移存量」**：
  ① 正向形态 `assert "<字面量>" in 源码` 无判据（会恒假、也会随搬迁静默失效），需单独立单；
  ② A 类 7 处随脚本版本退役自然消失，那是 `script-version-lifecycle` 的事，不需本族动作。
- **`tests/test_deploy_scripts.py` 循环 3 处**：其中 2 处已找到共同替代物（`>&2` / heredoc 目标形态），
  只有 `:125` 确实无锚点。若将来要动，先按第七批的 per-file 锚点表形状办。
- **`test_deploy_scripts.py:123` 的 `if literal == "city-b": continue`** 使 `"city-b"` 这条禁词从不生效
  （第六批发现，仍未修，属另案）。
- **运行期产物类（本批 3 处）要不要有一个正式名字**：它既不是轴一的「解析成结构」，也不是
  轴二的「tmp 路径可识别」，而是「产物路径经由被测接口返回」。现在靠 `origin=` 字符串表达；
  若这类位点再增长（第 2 个文件出现），再考虑给助手加一个显式的 `of_artifact()` 入口。
