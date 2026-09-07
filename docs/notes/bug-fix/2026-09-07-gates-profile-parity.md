# run_gates check:full/pr 覆盖对齐（#825）+ pr-migrate 本地 gate

Status: implemented
Class: bug-fix

## Decision

修复 #825（本地门禁声称的覆盖与 CI 实际不符——「本地全绿=PR 全绿」误导）：

1. **check:full 排除本机专属 gate**：新增显式排除表 `FULL_EXCLUDE = {"gov-skills"}`（数据源=本机 ~/.claude 会话转录，他机必红）；`check:full` 由「全部 GATES」改为「全部 − 排除表」。ai-drift 保留在 full（无 registry 数据的机器上 no-op 绿）；头部文档口径同步修正；
2. **check:pr 补 pr-migrate**（迁移回归是 PR 阶段唯一拦截点，#510/#644/#644 后续口径）：新 gate `tools/dev/check_pr_migrate.py` 复刻 CI 同名 job 两步——空 PostgreSQL 上 `alembic upgrade head` + `backend.scripts.check_schema_sync`（diff ⊆ 基线白名单）。**docker 可用则真跑**（postgres:16 一次性容器，拉取/启动失败同样 SKIP）；不可用则**显式 SKIP**（exit 0 + 大字注明以 CI 为准——能拦的环境拦、拦不了的不假绿）；S5x 登记锚点=`Migrate empty PostgreSQL database`；
3. **Registry dogfood**：本变更自身经 `ai_work.py` 全周期登记（declare → update --pr → merge 后 update=reconcile MERGED → finish），完成 `update --pr` 首次真实验证（P1 遗留 pending 项）。

## Alternatives

- **check:full 只改文档口径不排除**——放弃：他机确定性红灯是实际缺陷不是措辞问题；
- **pr-migrate 需 PG 故不进 check:pr**——放弃：issue 明确该缺口属 B 级（本地全绿推上去仍可能红）；SKIP 自检设计使其在无 docker 环境不劣化 DX；
- **复用 backend/tests 的 testcontainers**——放弃：conftest 面向测试套件；独立一次性容器脚本更贴近 CI job 形态、可单独超时与清理。

## Verification

- `check_pr_migrate.py --self-test`：env 构造（端口/TESTING/覆盖）与 SKIP 语义（mock docker 不可用→exit 0 + `[SKIP]` + 声明以 CI 为准）红绿双向全过；
- **docker 真跑（本机 26.1.5）**：postgres:16 一次性容器 → 空库全链 alembic upgrade head（90+ 迁移）→ schema 比对 `compare_metadata diff: 5 项（基线 5，新增 0）` → `[OK]` exit 0，容器已清理；
- `run_gates.py --list` 与 PROFILES 更新（quick 不变 / pr +pr-migrate / full −gov-skills）；治理门禁 S1–S11+S5x 全绿（S5x 接受 pr-migrate→ci.yml 锚点）；ruff 通过；
- Registry dogfood 全周期记录见 PR 描述（update --pr 的 PR_OPEN 与 merge 后 MERGED reconcile 首验）。

## Revisit

- SKIP 场景的 CI 侧兜底已存在（required check `pr-migrate-empty-db`）——无需补；
- `FULL_EXCLUDE` 未来增项须附「数据源物理仅本机」理由（S5x None 登记同款纪律）。
