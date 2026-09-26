# 新站 seed 参数面：全量核对 + 例外登记 + 基线棘轮守卫（#3289）

Status: implemented
Class: feature

关联：[#3289](https://github.com/DUElost/stability-test-platform/issues/3289)（验收③的
「显式登记 + 守卫测试」臂；修复路径 a/b 待 owner 按本核对清单裁决）、ADR-0051 v1.4、
ADR-0033 v1.15（#3203：`default_params` 归 `kind=script` 由 DB catalog 管）、ADR-0041、
#3347。前序执行 `adr0051-default-params-parity-3289`（codex，2026-09-25，FINISHED/NO_PR，
未落任何文件）经今日评论「有了清单即可领单」后转手，`--force` 留痕。

## Decision

- **核对（验收①，2026-09-26 全量重跑）**：隔离空库（stp-dev postgres 新建
  `stp_3389_probe`，禁碰生产写）alembic head + `sync_scripts_from_manifest`（站点包源
  只读）→ 与生产**只读**比对：活跃集 **107 = 107 零单侧差集**；缺键仅
  **`install_apk@1.0.1: ['apk_path']`** 1 版本 1 键；多键 0；同键值漂移 0。与 09-25
  首次核对结论一致且未随新版本注册增长（其间 +5 版本均两侧同步为空参数面）。
- **口径发现（本核对的方法论增量）**：新站 seed 参数面 ≡ **alembic head 面 ∩ active
  ∩ 非 manifest-retired**——scan（`sync_scripts_from_manifest`）对 `default_params`
  零写入（新建行恒 `{}`）、对 retired 既有行显式退役。因此该面由仓内事实
  （迁移族 + `tool_manifest.json`）唯一决定，**CI 可完全复现、不依赖站点包源**。
- **显式登记（验收③前半）**：`tests/fixtures/seed_default_params_face_3289.json`
  的 `registered_exceptions` 登记 install_apk@1.0.1/apk_path：理由 = 生产长期运营
  修正值从未进 seed、唯一消费 Plan #14 已于 09-25 受控迁移固化进 `step.params`
  （step.params 优先，运行时不依赖 default_params 兜底）；出口 = owner 在
  a（回灌 Git 侧 seed）/ b（受控导出物 + SOP）间裁决，裁前保持登记态。
- **守卫测试（验收③后半）**：`tests/test_seed_default_params_face_3289.py` 两条——
  ① seed_face 基线棘轮（testcontainer postgres + alembic head + manifest retired
  过滤 ⇒ 与基线逐键相等；任何 seed 参数变更必须显式更新基线）；② 登记例外诚实
  （例外键若被 seed 侧悄悄补上而登记未清即红）。

## Alternatives

- **回灌 install_apk@1.0.1 的 apk_path 进 seed 迁移（直接选 a 落地）**：不做——路径 a/b
  是 issue 明写的「核对后定夺」owner 决策点；且该值是生产运营数据，入 Git 前须 owner
  对「生产参数值进仓」表态（09-25 受控迁移也刻意未公开具体值）。
- **守卫断言 ⊇ 生产面**：需要生产参数面受控导出物做 CI 参照——同属 a/b 裁决前置；
  裁前以本次全量核对为人工基线，不在 CI 里假设。
- **基线含值**：弃——键名快照已足够钉语义面（值的漂移属运行数据），且避免把生产
  值批量带进仓。
- **沿用 codex 旧执行的 replay 文件名**：其文件从未落 main（NO_PR），不复用避免
  幽灵引用；本单文件名带 3289 直接对齐 issue。

## Verification

- `pytest tests/test_seed_default_params_face_3289.py` → **2 passed**（基线棘轮 +
  例外诚实；testcontainer postgres 真跑 alembic head）；
- **判别力实证**：首版测试按「全行面」口径对基线必红（多出 17 个 retired/inactive
  行的键集）——口径修正为 active ∩ 非 retired 后转绿，证明断言真实承重；
- **交叉验证**：基线生成自「alembic head + scan」探针库面，测试从「纯 head +
  manifest 过滤」独立复现，两者逐键相等——scan 零写参数面的结论被双向证实；
- 生产访问全程只读（SELECT），探针库为 stp-dev 栈内新建隔离库。
- **PR 离线门禁（#1707）**：`tests/test_seed_default_params_face_3289.py` 加入
  `ci.yml` / `run_gates.py` 的 `--ignore` 名单（与既有 testcontainer 文件同口径）；
  `pytest tests/test_offline_subset_guard.py` 全绿。夜间 `backend-test` 仍跑该文件
  （全量 `pytest tests/` 无 ignore）。

## Revisit

- owner 裁 a：apk_path 回灌 seed 迁移 + 基线 `seed_face` 加行 + 例外清偿（同一 PR）；
  裁 b：受控导出物进 `agent-host-onboard` SOP + 守卫补 ⊇ 断言。
- 新版本注册后若带 seed 参数（新 seed 迁移），基线随 PR 显式更新——棘轮保证这
  不会静默发生。
