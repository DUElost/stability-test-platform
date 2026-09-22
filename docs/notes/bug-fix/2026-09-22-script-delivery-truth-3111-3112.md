# 脚本「下发—生效」链路的两处真值缺口：账本覆盖边界（#3111）+ 载荷根未跟踪文件（#3112）

Status: implemented
Class: bug-fix

## Decision

两处缺口共用同一个根：**「脚本/载荷到底有没有到主机上」这件事，现有读取面要么没说、
要么说错**。2026-09-22 部署（控制面 `45c159cf`）现场暴露出来的形态是：`script-presence`
汇总报 `missing=0`，而 47 台主机其实没有当天合并的 `fill_storage` v1.1.1 文件。

### ① #3111：账本自报覆盖边界，未覆盖的 active 版本不再隐形

`script_presence` 的全集口径是 `plan_step(enabled) 引用 ∩ script.is_active`
（`build_full_target_set`，与 `expected_scripts_for_run` 同口径）。「已 active 但无任何 Plan
引用」的版本因此**一行都不写**，任何主机的可达集里都没有它 ⇒ 汇总里的
`counts.missing/mismatch = 0` 只覆盖 `full_versions`，却极易被读成「所有 active 版本在位」。

生产实测（只读查询 + 现算）：`|active| = 97`、`|full| = 50`、`uncovered = 47`，
`fill_storage` v1.1.1（#3085 合并，尚无 Plan 引用）就在其中；同刻 47 台主机的
`extra.agent_code_deployed = a23d3250`，而 `a23d3250` 的树里没有
`backend/agent/scripts/fill_storage/v1.1.1/`。

改法（只加面、不动既有判定）：

- `backend/services/script_presence.py`：新增纯函数 `active_unreferenced_versions()`
  （与 `build_full_target_set` 出自同一对集合、方向相反，两侧互补），`run_sweep` 在结果与
  日志里带计数（`uncovered_active` + 前 10 个预览）；**不折进五态**——「账本没验过」与
  「本机缺」是两件事，把 47 个无人引用的 active 版本一律判 missing 立刻是成片噪声，
  也会推翻既有「用可达集收敛告警面」的裁决；
- `backend/api/schemas/script_presence.py`：汇总新增 `uncovered_active_versions`，
  `full_versions` 仍是账本覆盖数（两数互补，读法写在字段描述里）；
- 前端 `types.ts` 同步（契约轴线 C 双向对拍），明细面板与「目标版本」并排显示
  「账本未覆盖 N」，使 `缺口 0 台` 不能再被读成「所有 active 版本都在位」；
- 测试：互补性双向对拍、#3085 形态（挂进启用步骤后必须从差集消失）、sweep 不落行 +
  日志断言、API 字段正反两次翻转。

### ② #3112：载荷根未跟踪文件 → 部署源守卫硬失败

热更新 tarball 与 desired digest **共用同一份枚举**（`host_updater._iter_payload_files`
与 `artifact_digest.collect_artifact_entries`，「digest 输入集 = 部署输入集」由同一份代码
保证，ADR-0040 D1），枚举根是 `backend/agent/`，走 `os.walk` ⇒ **未跟踪文件就是载荷内容**：
它随下次热更新下发到全部主机，并且改变 desired digest——而 digest 是
`agent_code_sync_status` 的唯一判据（ADR-0040 v1.1）。实测：在载荷根放一个临时文件，
desired digest 从 `sha256:020a7f12…` 变 `sha256:96ba11d1…`，删掉即复原；即 48 台生产主机
的同步徽标可被一个临时文件整体翻转，且与真漂移在同一枚徽标上不可区分。

守卫此前只把 **tracked** 脏工作区当硬失败，未跟踪文件一律 WARN（「可能是并发会话的临时
文件」）——那条容忍度对仓库根成立（systemd `WorkingDirectory` 与并行会话共享），对载荷根
不成立。改法：

- 新增 `tools/dev/check_payload_root_clean.py`：`git status --porcelain --untracked-files=all`
  限定载荷根，逐个列出未跟踪文件；`.gitignore` 覆盖路径（`resources/`、`__pycache__`）
  git 本就不报，不受影响；读不到 git 状态时 **fail closed**（未知不得当干净）；
- `tools/dev/check-deploy-source.sh` 把它接成**硬失败**（`if ! …; then … exit 1; fi`），
  排在仓库根未跟踪文件的 WARN **之前**；`--payload-root` 可覆盖（测试用）。

## Alternatives

- **#3111 把未引用 active 版本折进五态判 `missing`**：否决。可达集收敛是既有的、有实测依据
  的裁决（每族实跑 host 数 1–46 不均，51×48 会给假缺口），而 47 个版本乘 48 台会直接制造
  成片红灯，把真缺口淹掉；本改动只补「账本没覆盖」这一事实。
- **#3111 在摘要里逐个列出未覆盖版本**：否决。生产数量级是 47（噪声），而它要纠正的是一个
  读法——计数足以纠正，具体是哪些查 `GET /api/v1/scripts` 即可。
- **#3111 顺带核验未引用版本**：否决。账本走 `verify_scripts` RPC，主机侧按 manifest 逐项
  比对；把 97 个 active 版本全量发下去只是把载荷与行数翻倍，而没有任何消费方读这些行。
- **#3112 让载荷/摘要枚举只取 git 跟踪内容**：否决。枚举实现与主机侧镜像共用
  （`backend/agent/artifact_digest.py`，parity 由测试锁定），主机上没有 git；且
  `resources/` 大件本就是 gitignore + 带外布放的语义，改成 tracked-only 会连它一起切掉。
  守卫在部署入口硬拦是同一不变量、代价最小的落点。
- **#3112 只升级告警文案**：否决。改的正是「会不会被推上生产」这个后果，不是提示质量。

## Verification

- 载荷根检查器行为七例（临时 git 仓库，真跑子进程）：干净→rc 0；载荷根未跟踪文件→rc 1 且
  列出路径与三条处置出口；仓库根未跟踪（`.pid`）→rc 0；载荷根下 gitignore 路径
  （`resources/apk.bin`、`__pycache__/*.pyc`）→rc 0；嵌套未跟踪目录→逐文件列出；非 git
  仓库→rc 1 且无 Python 异常文本；真实仓库→rc 0。
- 守卫端到端（临时仓库装全套依赖 + `venv` 软链）：A 干净→rc 0 且 OK 行含「载荷根无未跟踪
  文件」；B 载荷根放临时文件→rc 1，先打 `check_payload_root_clean: FAIL …` 再打守卫 FAIL；
  C 移到仓库根→rc 0 且只剩 WARN；D 切 `feature-x` 分支→rc 1（老判据未被削弱）。
- `python -m pytest tests/test_payload_root_clean_3112.py -q` → 9 passed。
- `python -m pytest backend/tests/services/test_script_presence.py
  backend/tests/api/test_script_presence_api.py -q` → 15 passed（含新增 4 条）。
- `python -m pytest tests/test_api_response_shape_contract.py -q` → 15 passed（轴线 C
  含 `ScriptPresenceSummaryOut ↔ ScriptPresenceSummary`）。
- 前端：`npm run type-check` 干净；`npx vitest run
  src/components/network/ExpandableHostTable.test.tsx` → 28 passed（含新增断言
  「账本未覆盖 46」）。
- 真实库现算（只读）：`covered(full) = 50`、`uncovered = 47`、`fill_storage 1.1.1` 命中未覆盖集。
- 部署侧：本次 48 台热更新后（`SUMMARY ok=48 converged=1 fail=0 skipped=0`，逐台
  `agent_code_sync_status=matched`、revision `45c159cf`）才使 #3111 现场闭合。

## Revisit

- **#3111 未覆盖版本没有「是否在位」的事实面**：本改动只补计数。若后续需要逐版本判在位
  （例如「合并即校验」），要在 presence 加一层对未引用版本的 RPC 面——成本已在
  Alternatives 里量化，落地前先确认有消费方（告警或 UI），否则又是一笔只写不读的账。
- **#3112 只覆盖有仓库上下文的入口**（runbook / systemd / CLI）：UI 触发的热更新拿不到
  工作树判定，理论上仍可把未跟踪内容推上去。若之后出现该路径的真实事故，把同一判定搬到
  `batch_hot_update` 与路由层（届时需要设计「服务进程如何判定自身工作树」的语义）。
- **`resources/` 之外**的带外文件仍会被 rsync `--delete` 抹掉（`PROTECT_ONLY_PATHS` 只保护
  `resources/***`）；带外资源继续按「最终热更新之后放置」执行。
- 守卫新增硬失败会改变既有部署习惯：载荷根下任何未跟踪文件（含开发期临时产物）都会挡住
  部署。若实测出现「合法但未跟踪」的载荷文件，先回到本 Note 的 Alternatives 重新裁决，
  不要就地放宽成 WARN。
