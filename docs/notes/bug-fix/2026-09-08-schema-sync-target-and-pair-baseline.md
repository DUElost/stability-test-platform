# schema_sync 双缺陷修复：#934 生产配置覆写目标库 + #944 成对白名单放行索引丢失

Status: implemented
Class: bug-fix

## Decision

**#934（P1）**：`check_schema_sync._run_upgrade` 在 `command.upgrade` 前把目标库
写回 ambient `DATABASE_URL`。机制：`alembic/env.py` 模块导入即用环境解析结果
覆写 `config.sqlalchemy.url`，且 alembic.ini 自带 `sqlite:///./stability.db`
占位——「显式传入优先」不可行（生产 CLI 升级会静默打 sqlite）。ambient 是
`resolve_database_url` 解析顺序的最高位（env 源单一化设计），是唯一防住
「显式测试目标被 `.env.backend` 生产配置顶掉」的通道。

**#944（P2）**：`_filter_new_keys` 纯函数实现**成对白名单语义**——
`add_*`/`remove_*` 同对象同时进基线（autogenerate 把「改索引」渲染成
remove+add 两个 key 的既知噪音，如 `text("priority DESC")` vs
列名+`postgresql_ops` 的渲染差异），只在 diff **成对共现**时豁免；单边出现
（索引从库中完全丢失只产生 `add_index`）必须拦截。修正前该形态静默通过
（`diff ⊆ baseline` 的朴素语义盲区）。

## Alternatives

- **改 env.py 为「显式 URL 优先」**：否决——alembic.ini 的 sqlite 占位使其
  等价于「生产升级打 sqlite」，破坏 env 源单一化不变量；
- **删掉 alembic.ini 的占位 URL**：治标但触及生产 CLI 升级路径，回归面大，
  且 env.py 仍会对 ambient-DATABASE_URL 场景做多余覆写；脚本侧写回
  ambient 一行即达同一效果；
- **#944 清掉基线里的成对项**：否决——成对项是**活的**渲染噪音（实测同一次
  diff 产生两个 key），清了会天天红；正确语义是「成对豁免、单边拦截」；
- **修 model/migration 的表达式渲染差异根因**：两侧语义一致（都
  `priority DESC, enqueued_at ASC` partial），差异纯在 autogenerate 渲染层，
  无可修的真漂移（若未来收敛，`--rebaseline` 覆盖语义自然摘除成对项）。

## Verification

一次性 PG 容器（127.0.0.1:55432，端口错开生产）+ 合成 `.env.backend`
（假生产 URL 指向 127.0.0.1:1 不可达端口——若修复失败任何「打生产」
只会连接拒绝，物理碰不到真库）：

- **#934 E2E**：仅设 `TEST_DATABASE_URL`（issue 原场景）→
  `[alembic] target=postgresql+psycopg://…@127.0.0.1:55432/e2efix (from
  environment)`、迁移落库（alembic_version=k1l2m3n4o5p6）、检查绿 exit 0；
- **#934 反事实**：无 ambient 裸跑 `alembic current` → target echo 显示
  `(from /tmp/stp-b1b/.env.backend)` = 假生产——预修复的覆写机制被安全证伪；
- **#944 活体复现**：对已迁移库 `DROP INDEX idx_plan_run_admission_queue`
  后重跑 → diff 4 项、`[!! NEW] add_index|plan_run|idx_plan_run_admission_queue`、
  exit 1（修复前该形态静默通过）；
- **单测** `backend/tests/test_schema_sync_guard.py` 5 过（ambient 写回
  monkeypatch 捕获、成对豁免/单边拦截三态、对偶家族、真实基线语义）；
- `ruff check` 通过；`check:quick` 全绿。

## Revisit

- autogenerate 对 text() 索引表达式的渲染差异若在新 alembic 版本收敛，
  `--rebaseline` 会自动摘除成对项，本语义随之空转（无害）；
- `backend/core/database.py` 模块级 engine 在「仅 TEST_DATABASE_URL」场景
  仍会指向 .env.backend（import 期副作用，本脚本未使用该 engine）——
  未在本单范围，值得在 B2 迁移批次侧记。
