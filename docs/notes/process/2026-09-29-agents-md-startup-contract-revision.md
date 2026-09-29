# AGENTS.md 启动契约增量修订（#3516 的先行纠偏）

Status: proposed
Class: process

## Decision

对根 `AGENTS.md` 做一次**增量**修订（仍为五个固定章节、80 行预算内），只吸收 #3516 已复现、已核实的缺口，不改变
ADR-0034 §2.10「AGENTS.md = minimal bootstrap」的裁决：

1. **总原则**新增一条共享工作树纪律：不用 `git reset --hard` / `git stash` 清理现场，指向
   `repository-workflow.md`「Git 破坏性操作纪律」。此前该禁令只存在于 Claude 的 PreToolUse hook 与按需文档，
   其他 Harness 读根契约时看不到（#3516 A）。
2. **硬不变量**保留全部 8 条与 S11 锚点；仅把「业务表名使用单数」限定为「新建」并指向 `05-data-model.md`
   的复数历史例外（存量 7 张复数表，#3516 漂移项）。
3. **开始任务时**新增「分流」一步：默认单点实施；已属 ADR-0058 批次或明显命中 D7 强判据时，先读「批次交付流程」
   与载体 issue 的当前方案；需复核单元保持 draft，合入不等于生效。这是已 Accepted 的 ADR-0058 D7–D9 在入口处的
   传导补齐，不引入新语义。
4. 前检命令改为 `status --risk`（全量 `status` 输出 ~455 KB），并补 `finish` / `update --pr` 收尾。
5. **按需入口**把「批次规划与复核」拆为「批次交付流程」（实施者也需要）与「规划 / 复核做法」两行，
   并删去与「开始任务时」步骤重复的两行（DOC-MAP、执行契约）以保持 80 行预算。
6. **提交前**补「调用失败或超时不算通过」，jsdom 条目压成一行。

**刻意不做**（各有依赖，留给 #3516 对应分段）：
- 不把 `check:quick` 换成 `check:pr`：`run_gates.py` 的 agent-tests 仍裸跑 pytest，须先完成 G1 的内存硬顶；
- 不写 `.venv/bin/python`：项目 Python 入口待 G3 统一；
- 不改「云端写代码的工作面是否可成为实施者」——这是 ADR-0058 D8 / ADR-0034 的模型级变更，须先修订 ADR，
  不经 AGENTS.md 措辞带入；
- 不把 8 条领域硬不变量移出根文件（见 Alternatives）。

## Alternatives

- **把 8 条硬不变量迁出根文件、以 scoped 契约按需加载**（#3516 讨论中的「Hard ≠ Always-on」目标稿）：
  8 条合计仅 905 字节；`harness-adapters.md` 已记录 Zcode / CodeBuddy IDE 子目录只装载 scoped 文件、Codex / Cursor
  按 cwd 而非按触碰文件加载，「按目标文件渐进披露」并非跨 Harness 成立；仓库现只有 `backend/agent/` 与 `aee/` 两份
  scoped 文件，迁出意味着新增多份共享元文件；且目标稿 97 行超 S6 预算、S11 的 12 条锚点与 S5 的 required checks
  记载全部缺失，合入即红。收益小、风险大，暂不做；重议条件见 Revisit。
- **把 ADR-0058 的角色 / 批次语义整段写入根文件**：复述 D2/D8/D9 会让所有实施会话常驻只对批次单元相关的内容，
  且与「根入口不复制」冲突；改为只放分流一步 + 入口指针。
- **直接提高 S6 预算**：否，靠改写现有步骤与合并重复路由腾挪。

## Verification

- `python tools/dev/check_governance_surface.py --check --base origin/main` 与 `--self-test`（结果见 PR）；
- 修订前后逐条比对：S11 的 12 条锚串仍在场；S5 记载的六项 required check 仍在场；文件 79 行 / 6552 字节（预算 80 行 / 8000 字节）；
- `Pydantic`、`ASGI` 等不变量原文未改动（`git diff` 仅触及上列 6 处）；
- 未验证：Harness × cwd 的真实加载行为（#3516 G2 的探针矩阵负责）；云端容器无 `psycopg`，`check:quick` 交给 CI。

## Revisit

- #3516 G1 落地（pytest 统一硬顶）后，把提交前的 `check:quick` 与 `check:pr` 的覆盖关系写清；G3 定稳定 Python 入口后回填；
- 云端实施者裁决落地为 ADR-0058 / ADR-0034 修订后，同步「开始任务时」第 4 步的协调域表述；
- 若根文件再次贴 S6 预算、或出现「Harness 因常驻规则过多而漏规则」的实证，再按领域评估硬不变量的迁移，
  且须先在目标归属面建好并把 S11 锚点迁过去，再从根文件删除。
