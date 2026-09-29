# 新机接入 Skill 不再从开发检出触发 fleet 级热更新（#3537）

Status: implemented
Class: bug-fix

## Decision

`.claude/skills/agent-host-onboard/SKILL.md` §6 原先在单机 `POST /api/v1/hosts/<host_id>/hot-update`
之后并列给出「全 fleet 对齐」：`cd` 开发检出后运行 `batch_hot_update.py --direct`（注释自述
「无 --limit；会扫全部 ONLINE host」）。这有两处问题：

1. 把「接入一台新机」的意图扩大成 fleet 写操作；
2. 载荷来自开发检出：`backend/services/host_updater.py` 的 `_AGENT_SOURCE_DIR` 是相对模块
   文件解析的（`Path(__file__).parent.parent / "agent"`），CLI 在哪棵树里运行，载荷与
   `get_agent_code_version()` 就取自哪棵树。开发检出上的未提交/并行会话改动会被当生产载荷。

改动（只动 §6 文本，不改代码）：

- 单机 API 路径保留为默认，明确其只作用于路径里的那台 host；
- 删除从开发检出运行的 fleet 命令及其 `cd`；
- fleet 对齐改为一句显式意图说明：须显式意图、须先单机 canary、须从已核验发布根运行，命令与路径
  指向 `control-plane-deploy` §3，onboard skill 不复制易变事实（发布根路径等）。

涉及文件：`.claude/skills/agent-host-onboard/SKILL.md`、本 note。

## Alternatives

- **新增通用扫描 Gate（扫所有 skill/文档里「开发检出 + batch 脚本」的写法）**：放弃。目前只有
  一处命中，且 `capacity-p0-rollout-runbook` 中 `cd` 开发检出是构建 bundle 的合法步骤，
  规则难以在不误报的前提下区分「构建」与「分发」；为单点缺陷加 Gate 的维护成本高于收益。
  终态出口：若再出现同形态缺陷，再按 ADR-0058 D7 判据评估是否升级为批次。
- **改 `batch_hot_update.py` 本身（如加 `--host` 过滤、拒绝在非发布根运行）**：放弃，属非目标——
  本缺陷在 SOP 文本对意图的表达，而非脚本能力；改脚本会扩大到部署契约与测试面。
- **在 onboard skill 里保留 fleet 命令但把 `cd` 换成发布根路径**：放弃，会复制易变事实
  （发布根路径、`--include-active` 等语义），与 `control-plane-deploy` §3 形成第二份真相，
  日后必然漂移。

## Verification

- 静态代码阅读（**未运行任何测试**，本 PR 只改 skill 文本）：
  - `backend/api/routes/hosts.py` `host_hot_update(host_id, ...)`：`db.get(Host, host_id)` 取单台，
    只对该 `host.ip` 调 `execute_hot_update`，不枚举其他 host；
  - `backend/scripts/batch_hot_update.py::_hot_update_direct`：`Host.status == "ONLINE"` 且
    未退役的全量查询，`main()` 的参数只有 `--direct/--include-active/--abort-running-jobs/
    --retry-abort-pending/--force`，没有 host 过滤；
  - `backend/services/host_updater.py`：`_AGENT_SOURCE_DIR` 相对模块文件解析，载荷与版本取自
    进程所在代码树。
- `python3 tools/dev/check_governance_surface.py --check --base origin/main` 与 `--self-test`：
  结果见 PR 正文。
- `python scripts/run_gates.py check:quick`：pending（云端容器无 psycopg / pytest）。

## Revisit

- 若 `batch_hot_update.py` 增加单机/子集过滤或发布根自检，重议 §6 是否可给出受控的批量入口；
- `control-plane-deploy` §3 的 fleet 流程变更时，本处仅需保持指向，不需同步改动。
