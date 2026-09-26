# 待裁 issue 一轮收口（11 单）

Status: implemented
Class: architecture

## Decision

2026-09-26 对 open issue 做了一次「已裁 / 待裁 / 缺数据」分流，其中 11 单证据齐、选项已列清，
owner 当日授权 Claude **统一裁决**（同 2026-09-25 [ADR 待裁子项收口](2026-09-25-adr-pending-items-sweep.md) 的做法）。
本次只落裁决与文档，不改代码；各单裁决后即可由 harness 按常规领单实施。
代码事实均在 `origin/main @ 1e6c41a` 上读码核对。

| issue | 裁决 | 关键依据 |
|---|---|---|
| #3203 | **确认已由 ADR-0051 v1.3 族级 `kind` 裁定**：`kind=script` 注册为 `script` 版本行，DB catalog 是其唯一运行时权威；`kind=tool` 绝不建 `script` 行，Git manifest 是其唯一发布事实源。本 PR 同步回写 ADR-0033 D3（v1.15）与外部工具设计文档 §3/§6 | `script_catalog.script_entries` 只注册 `kind=script`（:168-175）；`tool_manifest.json` 现 35 script / 5 tool 族；ADR-0033 D3 正文仍写 YAML + 编译进 script 行，属文档漂移而非待裁方向 |
| #3093 | ① **关闭为已被取代**：`check_new_script_family.py` 已退役，D0 归类改由 manifest 族级 `kind` 机器可读登记承担（ADR-0033 v1.14）；② S15⑦「是否新建 ADR」改用 **相对 base 新增的 ADR 文件**（`git diff --diff-filter=A base...head`）判定，不再读 ADR 自填的头部日期；③ pin `EXCEPTIONS` 守卫的通过文案如实声明「只校验理由格式（含 `#`），不校验 issue 实在性」 | ① 文件已不存在；② CI checkout 为 `fetch-depth: 0`，base diff 可得，且与首次提交日期相比不受 rebase / cherry-pick 影响；③ 离线门禁无法核验 issue 状态，如实降级优于伪造判据 |
| #3094 | CI / gate 步骤如实改名为「Tool Contract fixture 自检」；**删除** `STP_VERIFY_TOOL_CONTRACT` 跳过开关（全仓零使用方）。接真实 `--entrypoint` 不在本期：**触发器** = 首个按 D2 契约实现的工具适配器族登记进 `tool_manifest.json` 时，同 PR 接线 | 现有 5 个 `kind=tool` 族（Start-Log-Scan、Scan-Result-GT、Monkey-Log-Scan-GT-SPRD、flashtool、aimonkey）均按 legacy 形态接入（ADR-0033 v1.2 legacy 例外登记，或 D7 的外部二进制工具），D2 契约只是新族准入要求，现在接线没有合法靶子；跳过开关无使用方，删除即不再需要把它纳入 env 清单 |
| #3204 | **实现 S15③ 的 diff 联动**（选项 1）：`check:pr` 通道中，ADR 头部版本变化且带 `归属域：` 行时，要求 `docs/design/2026-semantic-ownership.md` 出现在同 PR diff；`check:quick`（无 base）保持现状 | 门禁在 CI 已有全量历史；降级为人工纪律会让「已生效」判据退回散文，与 #3093 要消除的形态同族 |
| #2623 第 3 项 | 采纳 **3A**：`include_jobs` 默认保持 `True`；加 AST 守卫，禁止新增读取 PlanRun detail 内嵌 `jobs` 的消费点，白名单 = 现有调用方（含 `backend/scripts/seed_and_smoke.py` 的 `pr["jobs"]`）。守卫落地后关闭 #2623 | 调用方已清点完（issue 评论 2026-09-20/22）；零行为变化；3B 默认翻转会让仓外未知调用方静默变空 |
| #3077 | 采纳正文判据：同一 `plan_id` 的 run 终态时 `total_job_count ≥ 20` **且** `completed_job_count == 0` → 专属 kind + **warning**；同一 Plan **连续 2 窗** 0 产出 → **critical**。不恢复通过率判定轴、不改 `plan_run.status` | 只观测「执行链跑完而产出为 0」（平台/脚本故障），不观测设备失败，落在 ADR-0048 允许面内；N=20 排除单设备 smoke（run 502 形态），实施时可按现网分布微调并在 PR 写明 |
| #3285 | 选项 **②**：端点正规化为 `ApiResponse[ScriptScanOut]`，登记 `_MODEL_PAIRS` 双向对拍、撤销 `scan_scripts` 豁免条目，`types.ts` 新增同名具名接口 | 这是 `test_api_response_shape_contract.py` docstring 自述的路线，且能让下一次字段漂移被门禁拦下；UI 是否展示新键仍属产品决定，不在本单 |
| #3167 | **判据侧修**：`check_unreferenced_script_versions.py --guard` 在 usage 事实不可得时，先输出带 `guard.status=UNKNOWN` 的 JSON payload 再 `return 2`；probe 侧判据不变；补一条走真实 CLI 出口的用例 | 与 probe 契约一致，改一处即可，且消除测试里「理想形状」掩盖不可达的问题 |
| #3353 | 选项 **1** 为主：compose 中口令类键（`STP_ADMIN_PASSWORD`、`POSTGRES_PASSWORD`）改为 `${VAR:?…}` 强制引用，未设即启动报错；非机密键（`STP_ADMIN_USER`、库名）可保留默认；`local-development.md` 写明 compose 插值只读 `.env` 与 shell；另加正文第 3 条：`init_dev_db.py` 检测到 `admin123` 时打印显式告警 | 「没配」从静默回落变成启动即报错；不删 `environment:` 行，避免 `DATABASE_URL` 容器内地址与宿主直连重新耦合（正文「不建议」项） |
| #3333 | ② 与 ① **都做**：`run_sweep` 在本进程 socketio server / agent registry 未就绪时**拒跑**（fail-closed，CLI 误跑不再打脏列与账本）；另加 `POST /script-presence/refresh-all`（`require_admin` + 简单节流），给 gauge 验证与告警上线一个入口 | ② 是本质修法，① 解决「部署后只能等 cron」的验证缺口，两者不互斥 |
| #3356 | 本期做 **候选 2 + 3**：`agent_env_sync` 增退役键表 `RETIRED_ENV_KEYS`（与 `tests/test_removed_env_keys.py` 台账同源），同步时从主机 `.env` 删除这些行；部署 SOP 与部署前检查脚本补判据「diff 触及渲染键 ⇒ 必须 `--force`」。候选 1（env 层 digest 并入收敛判据）改变 ADR-0040 收敛语义，**本期不做**，带复议触发器 | ADR-0051 D7 第 4 片（撤 `STP_FLASH_TOOL_DIR` 注入）与 `unisoc-env-path-keys` 过渡项的删键下发都依赖候选 2；候选 2 不改收敛语义，无需先改 ADR-0040 |

**偏离原草案 / 原文**：无。11 单的裁决都取自 issue 正文或评论中已列出的选项（多选项的取推荐项或组合项）；
#3094 删除跳过开关、#3093 ② 用 base diff 而非首次提交日期，都属正文「可选方向」内的具体化。因此不另开 owner 确认单。

本次改动的文件：`docs/adr/ADR-0033-tool-kit-ecosystem-integration.md`（v1.15，D3 按对象拆分回写）、
`docs/adr/README.md`（ADR-0033 行）、`docs/design/2026-09-external-tools-integration-and-package-architecture.md`（§3.1–§3.3、§6）、
`docs/design/2026-semantic-ownership.md`（`script-runtime-catalog` 行范围注明仅 `kind=script`）、本 Note。
各单的裁决同步回写到对应 issue，并摘除 `needs-decision` 标签（#3093、#3094）。

## Alternatives

- **#3203 另立新 ADR 做对象拆分**：拆分已由 ADR-0051 v1.3 裁定并落地，再立一份会形成同主题第二份权威（execution-contract §3.5 禁止）；只回写 ADR-0033 正文。
- **#3094 立即接真实 entrypoint 扫描**：现有 `kind=tool` 族都不实现 D2 契约，接上只会全红或需要逐族豁免，豁免本身又是 #3093 那一族的形态。
- **#3204 降级为人工纪律**：成本最低，但 S15③ 目前被文档宣称为「已生效」，降级要同时改写多处文档，且留下一个无机器判据的承诺。
- **#2623 翻转默认值（3B）或删字段（3C）**：3B 对仓外调用方是静默变空；3C 属破坏性契约变更，需对外版本策略，本仓尚无。
- **#3356 候选 1（env digest）**：更彻底，但要修订 ADR-0040 的收敛语义并改热更新判据；在删键下发这一实际阻塞面上，候选 2 已足够。
- **#3353 删除 `environment:` 行改靠 `env_file` 单源**：正文已列为不建议——会让 `DATABASE_URL` 的容器内地址与宿主直连场景耦合。

## Verification

- 读码锚点（`origin/main @ 1e6c41a`）：`backend/services/script_catalog.py:168-175`、`tool_manifest.json`（族级 `kind`）、
  `tools/dev/verify_tool_contract.py:33-35/:186-190`、`scripts/run_gates.py:121-122`、`.github/workflows/ci.yml:177/:248-249`、
  `tools/dev/check_governance_surface.py:825-826/:930`、`tests/test_pipeline_template_script_pins_2865.py:77/:97`、
  `backend/services/agent_env_sync.py:181`、`tests/test_removed_env_keys.py`、`backend/scheduler/cron_scheduler.py:125`；
  `tools/dev/check_new_script_family.py` 已不存在（ADR-0033 v1.14）。
- 在飞冲突检查：`ai_work.py declare` 以 11 个 `--issue` 登记，查重无冲突；在开 PR 仅 #3392（不触碰本次文件）。
- 门禁：`python scripts/run_gates.py check:quick`，结果见 PR 描述。

## Revisit

- #3094：首个按 D2 契约实现的工具适配器族登记时，同 PR 接真实 `--entrypoint` 扫描。
- #3356 候选 1：出现第二次「env-only 变更未随热更新到达主机」的实例，或 ADR-0040 digest 面另有修订需求时，重开 env 层 digest。
- #3077：上线满 30 天后按实际误报 / 漏报回看 N=20 与「连续 2 窗」两个参数。
- #2623：若将来做对外 API 版本化，再评估 3C（删字段）。
