# ADR-0038 ①：host 退役 schema/模型/迁移落地（#1800）

Status: implemented
Class: architecture

## Decision

按 ADR-0038 v0.2（Accepted）实现分解 **①/6（迁移与模型）**，只落数据面，不带行为：

1. **迁移** `f3a4b5c6d7e8_host_retirement_columns.py`（down_revision `a3b2c1d0e9f8`）：
   `host` 表 additive nullable 新增四列——`retired_at TIMESTAMPTZ`（D1：
   非空即退役）/ `retired_by VARCHAR(128)` / `retire_reason TEXT` /
   `retire_alerted_at TIMESTAMPTZ`（D4 心跳告警去重载体，与 D1 三列同批）。
   **无回填、不新增索引、不改 FK**（ADR §4；drop 列连带删索引是 #644 事故形态）。
   docstring 显式写明「downgrade 会丢失全部退役状态且退役主机重新进入可派发集合」。
2. **ORM** `backend/models/host.py`：四列（`DateTime(timezone=True)` 拼写对齐既有列）。
3. **Pydantic** `backend/api/schemas/host.py`：
   - `HostOut` 增退役四列 + D6-(a) 身份当前值 `boot_id` / `agent_instance_id`；
     ORM 属性名是 `last_agent_instance_id`，用
     `validation_alias` + `populate_by_name` 桥接（交付面字段名保持
     `agent_instance_id`，先例 `schemas/schedule.py`）；
   - 新增 `HostRetireIn` / `HostUnretireIn`：`retire_reason` 必填、`min_length=1`
     （D2 审计 who/when/reason 的 reason，两端点对称）。
4. **前端类型** `types.ts` 的 `Host` 同步四列 + 两身份字段（硬不变量：类型入口与
   后端 schema 同步）。

**命名与顺序**：revision id 全局唯一经脚本比对 130 个既有 id 后选取（首次选取
`c9d0e1f2a3b4` 与既有 `seed_flash_firmware_v133_params` 撞车、曾出现双头，已换）。
`alembic heads` 实读单头。

## Alternatives

- **复用 `status=RETIRED` 枚举**：ADR §3 已否决（心跳会静默撤销生命周期）——本单
  不再重复论证，只落 ADR 决定；
- **`deleted_at` 软删列**：ADR §3 已否决（命名暗示可 purge，与「历史终态」矛盾）；
- **`retire_reason` 用 VARCHAR(n)**：弃——原因文本长度不可预知，ADR D1 明示 Text；
  对应地请求 schema 只约束非空、不设上限；
- **unretire 请求体字段另起名（如 `reason`）**：弃——两端点审计同构，统一
  `retire_reason` 便于 ② 复用同一模型语义；如 ② 实做时认为语义别扭，改名的
  成本仅是 schema 一处（已在本 Note 标注为对外契约点）；
- **本单顺带做 ② 的 retire/unretire 端点**：弃——issue 明确「本单只落
  schema/模型/类型」，端点含 Cordon 前置与审计 fail-closed，属 ② #1801；
- **新增索引（如 `retired_at`）**：弃——ADR §4 明示本次不新增索引；默认列表过滤走
  存量数据量下无碍，需要时另案加。

## Verification

- **迁移往返（真实容器，postgres:16 本机镜像）**
  `backend/tests/migration/test_host_retirement_roundtrip_1800.py`（2 例）：
  - 离线：`alembic heads` 实读**单头**且等于本 revision；
  - 容器：`upgrade head` → 四列存在且 `is_nullable=YES`、插入行 `retired_at`
    默认 NULL（无回填）→ `downgrade -1` 四列撤销 → 再 `upgrade head` 恢复；
  - 结果：**2 passed**（10s）；
- **schema 契约** `backend/tests/api/test_host_retirement_schemas_1800.py`（8 例）：
  `HostOut` 退役/身份字段默认与赋值往返、`retire_reason` 空值/缺省均 ValidationError、
  非空通过 → **8 passed**；
- **回归**：`backend/tests/api/test_hosts.py`（37 例）全绿，合计 **47 passed**
  （host 路由对 HostOut 增列不敏感，无断言破坏）；
- **schema-sync**：一次性容器上 `alembic upgrade head` 后运行
  `python -m backend.scripts.check_schema_sync` → **exit 0**（未动 baseline、
  未用 `--rebaseline`；输出仅存量基线噪音项）；
- `npm run type-check`（types.ts 同步）→ 通过；`ruff` → All checks passed；
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (7 gates)`。

未做：②–⑥ 的行为（Cordon 前置、列表过滤、派发收口、心跳告警）按 issue 顺序另单。

## Revisit

- **② 对接契约**（#1801 需知）：请求体统一用 `retire_reason`（min_length=1）；`HostOut`
  的 `agent_instance_id` 已由 `last_agent_instance_id` 桥接，路由侧 `_host_to_out`
  无需额外赋值；
- **downgrade 语义损失**：回滚会丢退役状态（列被 drop）——生产回滚前必须评估；
  若将来需要「回滚保留状态」，需改为新建列 + 双读过渡，属独立决策；
- **索引**：`retired_at` 过滤目前无索引；③ 的列表过滤上线后若 `GET /hosts` 出现
  慢查询，按 ADR §4 另开索引迁移（不与本单同批）；
- **D6-(a) 换机语义**：`boot_id` / `agent_instance_id` 已进交付面，但「同 IP 同 id
  的换机 = unretire」的可视化（详情与审计差异展示）属 ④/⑥ 范围；
- **revision id 撞车教训**：本单首次取 id 时撞上既有 `c9d0e1f2a3b4`，`alembic heads`
  立即暴露双头（好在未提交）。后续新迁移建议固定执行「比对全部既有 revision id」
  这一步（本单以脚本完成，未新增门禁——如需自动化可并入 `check_pr_migrate` 前检）。
