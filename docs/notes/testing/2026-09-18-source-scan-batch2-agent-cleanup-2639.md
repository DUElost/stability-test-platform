# 退役面源扫描守卫第二批迁移：16 处空守改先证锚点，并把棘轮判据从「文件」收紧到「断言」（#2639 第二批）

Status: implemented
Class: testing

- 日期：2026-09-18
- 关联：`#2639`（本族立单，已 CLOSED，本批为后续存量迁移）、`#2663`（同思路：可派生量不许抄成文字）、
  `#2641`/`#2642`（同思路：判据必须落在代码行上，注释里的同形文本不算）、
  `#735`（Agent 脚本化改造：本批守的正是那次清理的退役面）、`#2196`（watcher 收编 `scan_aee`/`export_mobilelogs`）
- 落点：`backend/agent/tests/test_legacy_tool_cleanup.py:1`、`tests/test_source_scan_anchor_ratchet.py:1`

## Decision

**本质问题（沿用 #2639 的判断，不重复论证）**：`src = path.read_text()` + `assert "词" not in src`
这类否定断言，在被扫逻辑搬走后**不会变红，只会变成恒真的空守**——它还跑、还绿，覆盖是零。
补法只有一个：判「不存在」之前先证「扫的还是那台真机」（正锚点），两类红分开报因
（`AnchorDrift` = 用例过期该改指新真源；`FormRegression` = 防线真回归）。

**本批范围 = 1 个文件 16 处（不是 declare 时估的 4 个文件 23 处）**：
`backend/agent/tests/test_legacy_tool_cleanup.py` 的 7 个函数扫 11 个**活动文件**
（`frontend/src/router/index.tsx`、`frontend/src/utils/socketEvents.ts`、
`frontend/src/hooks/useRealtimeDashboard.ts`、`backend/realtime/socketio_server.py`、
`backend/tests/api/test_plan_run_aggregation_endpoints.py`、`docs/archive/plans/*.md`、Agent 脚本与部署文档）。
这些路径正是会因 #1520 那类搬迁而漂移的面——#2639 三个实证里第 3 例的失效机制在这里逐条复现。

**剩下 3 个 `backend/agent/tests/` 文件（7 处）不做，且不是「来不及」**：
`test_device_flash_scripts.py`(5) / `test_flash_firmware_v1316.py`(1) / `test_flash_preflight_v102.py`(1)
的锚点落在 `backend/agent/scripts/<name>/v<version>/` 内，而硬不变量规定已发布版本目录**不可原地修改**——
真源不会漂，锚点漂移的风险面接近零，收益远低于本批。留作第三批（连同它「收益为何低」的判据一起，
避免下一轮又按文件数平推）。

**中途改了一处棘轮自身的设计（这是本批唯一超出「迁移」的动作，理由如下）**：
原判据是「该文件只要 `import` 了助手就整文件豁免」。这让覆盖方向**反了**——把某条
`assert_absent` 退回裸 `assert ... not in text` 时，因为文件仍导入助手，反而**变成免检**；
迁移越彻底，越可以藏。第二批恰好把 16 处集中进一个文件，这个洞的后果被放大到最大，
所以它属于本批「防线是否成立」的一部分，不是顺手重构。
实测改前/改后命中完全一致（**0 个额外文件、59 处不变**），即该豁免今天是死代码，只有绕过价值。
改后判据的最小单位是**断言**：`EXEMPT` 只留「描述该形态」的两个元文件，
且 `test_exempt_list_is_not_self_defeating` 钉死不得扩大；`test_detector_discriminates`
新增 `regressed.py` 夹具（导入助手 + 残留一条裸否定断言 = 必须命中）把它变成永久判据。

**同时替换掉一颗被本批撞红的存量钉子**：`test_scan_is_load_bearing` 原按
「`backend/agent/tests/test_legacy_tool_cleanup.py` 必须在 offenders 里」担保扫描走到了该目录——
这是把判据钉在存量上，该文件迁移完成后它必然红。换成与存量无关的口径：
`iter_candidates()` 单独证「每个扫描根都有候选」，另加
`test_scan_dirs_is_not_narrowed()` 钉住 `SCAN_DIRS` 只能扩大不能缩小
（变异实测：只加前者会被「直接删配置项」绕过，两者缺一不可）。

**计数不再抄进文字**（承接 #2663）：docstring 里的「28 文件/78 处、基线剩 26/75」是立单当日快照，
抄在现状口径文本里就没人约束它，已删除；现状只有 `BASELINE` 成员与当场数出的命中数两处。
`SITE_FLOOR = 50` 明确标注为**下限**（判据被削弱时兜底），不是现状计数，故存量迁移不会撞红它。

## Alternatives

- **按文件数平推 4 个文件 23 处**（declare 时的原计划）：否。脚本版本目录不可变 ⇒ 那 7 处
  没有漂移面，为凑批次数量去改它，是给守卫加仪式感而不是加覆盖。
- **给 `test_legacy_tool_cleanup.py` 保留整文件豁免，仅在 CI 加一条「新写的别学旧写法」评审**：否。
  评审不是判据，而 `regressed.py` 夹具可以廉价地把同一件事变成判据。
- **删掉文件级豁免但顺手把 `imports_helper()` 留给「以后统计迁移进度」**：否。没有调用方的
  判据函数会被下一次「复用它」的改动复活成洞；迁移进度用 `BASELINE` 长度表达即可。
- **把 `SCAN_DIRS` 钉子写成「每个根的 offender 数不得下降」**：否。这又是钉在存量上，
  与刚拆掉的那颗同类。改成钉「配置成员集合」+「候选可达性」，两者都与存量无关。

## Verification

- 只跑实际执行过的：`.wt/stp-2639-b2`（base `e40f7a13`）内
  `env -u DATABASE_URL …/python -m pytest backend/agent/tests/test_legacy_tool_cleanup.py -q` → **18 passed**；
  `… tests/`（根，required check 路径）→ **1546 passed, 11 warnings**；
  `… backend/agent/tests/` → **2145 passed**（69.44s）；
  `… tests/test_source_scan_anchor_ratchet.py tests/test_source_anchor_helper.py` → **14 passed**（5+9）。
- 棘轮口径核对（当场派生，不是抄的）：`scan_offenders()` = **25 文件 / 59 处**，与下调后的
  `BASELINE` **集合完全相等**（迁移前 26 文件 / 75 处）。
- **7 条变异，逐条判红且红在正确的因上**，全部还原后复跑基线绿；`git status --porcelain` 只剩本批 2 个文件：
  1. 锚点改成不存在的类名 → `AnchorDrift`；
  2. 已退役脚本名塞回被扫方案文档 → `FormRegression`；
  3. 已迁移文件退回一条裸 `assert ... not in text` → 棘轮 growth 红（**旧设计下这条是绿的**，即本批要补的洞）；
  4. 把本批已迁移文件留在基线里 → 棘轮 stale 红（证明「下调」不是可选动作）；
  5. 反向误删一条仍在场的基线项 → 棘轮 growth 红；
  6. `SCAN_DIRS` 删掉 `backend/agent/tests` → `test_scan_dirs_is_not_narrowed` 红；
  7. `anchored()` 去掉、无锚点直接 `assert_absent` → `GuardMisuse` 红（恒真入口仍是封死的）。
- 门禁：`python scripts/run_gates.py check:quick` / `check:pr` 结果见 PR 与本单回执评论（未跑完即标 pending，不预告结果）。

## Revisit

- **第三批**：`backend/agent/tests/` 余 3 文件 / 7 处。开工前先复核本批的否证——
  「锚点在不可变版本目录 ⇒ 无漂移面」若被推翻（例如脚本被复制成 v+1 后守卫指向旧版本目录，
  真源其实在新版本），它才升格为值得修；否则宁可不修，也不要用批次数量换覆盖率数字。
- 全家族剩余：22 文件 / 52 处（`BASELINE` 为准）。优先级应按**被扫文件是否会搬家**排，
  而不是按处数：`backend/tests/test_ci_and_test_harness_files.py`(5)、
  `tests/test_site_install.py`(4)、`tests/test_update_agent_playbook.py`(4) 扫的都是活动源。
- 判据本身的已知边界（不在本批扩大）：只认 `read_text`/`getsource` 两种读取口，
  `Path.open().read()`、`inspect.getsource` 之外的间接封装不命中；
  且「同一函数内把源码读进变量后只判正向量」不在射程内（那是 #2643 建议 3 的抓取面问题，不是本棘轮的）。
  下一次扩判据前先数一次增量，别边改边定口径。
