# 数据库规范：Schema 定义与迁移

> 使用 SQLAlchemy 2.0 + Alembic 迁移，数据库：PostgreSQL 15

## 完整 Schema

```sql
-- ─── Tool Catalog ─────────────────────────────────────────────
CREATE TABLE tool (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    version         VARCHAR(32)  NOT NULL,
    script_path     TEXT         NOT NULL,
    script_class    VARCHAR(128) NOT NULL,
    param_schema    JSONB        NOT NULL DEFAULT '{}',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    description     TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (name, version)
);

-- ─── Host & Device ────────────────────────────────────────────
CREATE TABLE host (
    id                    VARCHAR(64)  PRIMARY KEY,  -- 自定义 ID，如 "host-bj-01"
    hostname              VARCHAR(256) NOT NULL,
    ip_address            VARCHAR(64),
    tool_catalog_version  VARCHAR(64),               -- 当前已同步的 Tool Catalog 版本 hash
    last_heartbeat        TIMESTAMPTZ,
    cpu_quota             INTEGER      NOT NULL DEFAULT 2,  -- 最大并行分析进程数
    status                VARCHAR(32)  NOT NULL DEFAULT 'OFFLINE',
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE device (
    id          SERIAL PRIMARY KEY,
    serial      VARCHAR(128) NOT NULL UNIQUE,         -- adb serial
    host_id     VARCHAR(64)  REFERENCES host(id),
    model       VARCHAR(128),
    platform    VARCHAR(64),                          -- 如 "MTK", "UNISOC"
    tags        JSONB        NOT NULL DEFAULT '{}',
    status      VARCHAR(32)  NOT NULL DEFAULT 'OFFLINE',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─── Workflow Definition（编排蓝图）────────────────────────────
CREATE TABLE workflow_definition (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(256) NOT NULL,
    description         TEXT,
    failure_threshold   FLOAT        NOT NULL DEFAULT 0.05,
    created_by          VARCHAR(128),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE task_template (
    id                      SERIAL PRIMARY KEY,
    workflow_definition_id  INTEGER      NOT NULL REFERENCES workflow_definition(id) ON DELETE CASCADE,
    name                    VARCHAR(256) NOT NULL,
    pipeline_def            JSONB        NOT NULL,   -- 见 ARCHITECTURE.md §5 格式规范
    platform_filter         JSONB,                   -- 如 {"platform": "MTK"}，null 表示不限
    sort_order              INTEGER      NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─── Workflow Run（动态执行）────────────────────────────────────
CREATE TABLE workflow_run (
    id                      SERIAL PRIMARY KEY,
    workflow_definition_id  INTEGER     NOT NULL REFERENCES workflow_definition(id),
    status                  VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    -- SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED / RUNNING
    failure_threshold       FLOAT       NOT NULL DEFAULT 0.05,
    triggered_by            VARCHAR(128),
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at                TIMESTAMPTZ,
    result_summary          JSONB       -- 聚合报告，Reduce 完成后写入
);

CREATE TABLE job_instance (
    id              SERIAL PRIMARY KEY,
    workflow_run_id INTEGER      NOT NULL REFERENCES workflow_run(id),
    task_template_id INTEGER     NOT NULL REFERENCES task_template(id),
    device_id       INTEGER      NOT NULL REFERENCES device(id),
    host_id         VARCHAR(64)  REFERENCES host(id),
    status          VARCHAR(32)  NOT NULL DEFAULT 'PENDING',
    -- PENDING / RUNNING / COMPLETED / FAILED / ABORTED / UNKNOWN / PENDING_TOOL
    status_reason   TEXT,                -- Watchdog 原因、失败 step_id、tool 版本等
    pipeline_def    JSONB        NOT NULL,  -- 快照，冻结执行时的 pipeline 版本
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE step_trace (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER      NOT NULL REFERENCES job_instance(id),
    step_id         VARCHAR(128) NOT NULL,   -- 对应 pipeline_def 中的 step_id
    stage           VARCHAR(32)  NOT NULL,   -- prepare / execute / post_process
    status          VARCHAR(32)  NOT NULL,   -- PENDING / RUNNING / SUCCESS / FAILED / SKIPPED
    event_type      VARCHAR(32)  NOT NULL,   -- STARTED / COMPLETED / FAILED（幂等去重用）
    output          TEXT,
    error_message   TEXT,
    original_ts     TIMESTAMPTZ  NOT NULL,   -- Agent 侧原始时间戳，用于 Reconciliation 重建
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, step_id, event_type)    -- 幂等约束
);

-- ─── 索引 ──────────────────────────────────────────────────────
CREATE INDEX idx_job_instance_status      ON job_instance(status);
CREATE INDEX idx_job_instance_workflow    ON job_instance(workflow_run_id);
CREATE INDEX idx_job_instance_host        ON job_instance(host_id);
CREATE INDEX idx_step_trace_job           ON step_trace(job_id);
CREATE INDEX idx_host_last_heartbeat      ON host(last_heartbeat);
CREATE INDEX idx_device_host              ON device(host_id);
```

## 重要约束说明

**`step_trace` 的唯一索引**
```sql
UNIQUE (job_id, step_id, event_type)
```
这是 Reconciliation 幂等性的数据库层保障。Reconciler 在插入前检查，数据库层兜底防止并发写入重复记录。

**`job_instance.pipeline_def` 快照设计**
Job 创建时将当时的 `task_template.pipeline_def` 完整拷贝一份存入 `job_instance.pipeline_def`，而不是运行时再引用 template。这样即使 template 被修改，历史 Job 的执行记录仍然准确。

**`workflow_run` 中的 `failure_threshold` 冗余存储**
同样是快照设计，防止 WorkflowDefinition 修改阈值后影响已执行的 Run 的聚合结果。

## 迁移规范

```bash
# 创建迁移
alembic revision --autogenerate -m "add_job_instance_pending_tool_status"

# 执行迁移
alembic upgrade head

# 回滚
alembic downgrade -1
```

**命名约定**：迁移文件名使用小写 + 下划线描述变更内容，禁止使用 `"fix"` 或 `"update"` 等模糊名称。
