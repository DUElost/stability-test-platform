# 测试前置步骤与资源管理 — 完整设计方案

## Context

### 问题

稳定性测试平台中，大多数测试（Monkey、性能、压力等）都需要相同的前置步骤：联网 → 安装应用 → 导入测试资源（大量音频、图片）。当前架构下：

1. **重复定义**：每个 TaskTemplate 的 `pipeline_def.prepare` 都要 copy-paste 相同步骤
2. **重复执行**：一个 WorkflowRun 在 N 台设备上跑 M 个 TaskTemplate，同一设备上 setup 执行 M 次，每次 push 数 GB 资源
3. **版本缺失**：运维脚本散落各处，无统一版本管理，`version: "discovered"` 无实际意义
4. **无资源完整性校验**：push_resources 每次都全量推送，不管设备上是否已存在

### 目标

- 前置步骤定义一次，Workflow 内所有 TaskTemplate 共享
- 设备级幂等：已完成的准备操作不重复执行
- 资源传输有完整性校验，避免冗余推送
- 脚本统一存储在 NFS，平台管理元数据和版本

---

## 总体架构

```
┌─ 平台端 (Windows) ──────────────────────────────────────────────┐
│                                                                   │
│  WorkflowDefinition                                              │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ setup_pipeline    ← 新增：设备级前置步骤（继承 resolve 后入 Job）│ │
│  │ teardown_pipeline ← 新增：设备级后置步骤                       │ │
│  │ TaskTemplate[]                                              │ │
│  │   └── pipeline_def  ← 只保留 execute/post_process 步骤        │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  script 表（新增）                                                 │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ name, version, script_type, nfs_path, content_sha256         │ │
│  └─────────────────────────────────────────────────────────────┘ │
│         │                                                         │
│         ▼                                                         │
│  GET /api/v1/scripts              ← Agent 全量拉取元数据          │
│  POST /api/v1/scripts/scan        ← 扫描 NFS 目录自动注册         │
│                                                                   │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─ NFS 172.21.15.4 ────────────────────────────────────────────────┐
│  /mnt/storage/test-platform/                                      │
│  ├── apks/                                                        │
│  │   └── app_under_test_v1.2.3.apk                                │
│  ├── bundles/                                                     │
│  │   ├── audio_stability_v2/                                      │
│  │   │   ├── manifest.json           ← 文件清单 + sha256          │
│  │   │   └── bundle.tar.gz                                       │
│  │   └── image_stability_v3/                                      │
│  │       ├── manifest.json                                       │
│  │       └── bundle.tar.gz                                       │
│  └── scripts/                                                     │
│      ├── device/connect_wifi/                                     │
│      │   └── v1.0.0/connect_wifi.sh                              │
│      ├── app/install_apk/                                         │
│      │   └── v1.0.0/install_apk.sh                               │
│      └── resource/push_bundle/                                    │
│          └── v2.0.0/push_bundle.py                               │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─ Agent 节点 (Linux) ─────────────────────────────────────────────┐
│  启动时: 拉取 script 元数据 → 缓存到 SQLite                        │
│  执行时: resolve script → subprocess 调用 NFS 脚本                 │
│  心跳时: 上报 script 版本摘要 → 平台检测过期 → Agent 重新拉取       │
│                                                                   │
│  Step 幂等执行：                                                   │
│    1. 检查设备当前状态（WiFi / 已安装包 / 资源 manifest）           │
│    2. 目标状态已达成 → SKIPPED                                    │
│    3. 未达成 → 执行 → 记录完成状态                                │
└──────────────────────────────────────────────────────────────────┘
```

---

## 组件 1：NFS 目录结构

```
172.21.15.4:/mnt/storage/test-platform/
│
├── apks/                           # 被测 APK（文件即版本）
│   └── {app_name}_{version}.apk
│
├── bundles/                        # 测试资源包
│   └── {bundle_name}/
│       ├── manifest.json           # 文件清单 + sha256 + 版本号
│       └── bundle.tar.gz           # 打包资源（含 manifest，设备端可独立校验）
│
└── scripts/                        # 运维脚本
    └── {category}/
        └── {script_name}/
            └── v{major}.{minor}.{patch}/
                └── {entry_point}.{sh|py|bat}
```

### bundle/manifest.json 格式

```json
{
  "name": "audio_stability_v2",
  "version": 2,
  "bundle_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "total_size_bytes": 3221225472,
  "file_count": 2000,
  "files": [
    { "path": "audio/music_001.mp3", "sha256": "abc123...", "size": 5242880 },
    { "path": "audio/music_002.mp3", "sha256": "def456...", "size": 3145728 }
  ]
}
```

---

## 组件 2：Script 表与版本管理

### 2.1 数据模型（新建表）

```python
# backend/models/script.py

class Script(Base):
    __tablename__ = "script"

    id             = Column(Integer, primary_key=True)
    name           = Column(String(128), nullable=False)    # 如 "push_bundle"
    display_name   = Column(String(256))                    # 前端展示名
    category       = Column(String(64))                     # device / app / resource / log
    script_type    = Column(String(16), nullable=False)     # python | shell | bat
    version        = Column(String(32), nullable=False)     # 语义化版本 1.0.0
    nfs_path       = Column(String(512), nullable=False)    # 脚本在 NFS 上的绝对路径
    entry_point    = Column(String(256))                    # Python: "module:func"；shell/bat: 空
    content_sha256 = Column(String(64), nullable=False)     # 版本锚点
    param_schema   = Column(JSONB)                          # 参数 JSON Schema（前端表单 + 校验）
    is_active      = Column(Boolean, default=True)
    description    = Column(Text)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_script_name_version"),
    )
```

### 2.2 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/scripts` | 全量列表（Agent 启动拉取），支持 `?is_active=true` |
| `GET` | `/api/v1/scripts/{id}` | 单个脚本详情 |
| `POST` | `/api/v1/scripts` | 手动注册脚本 |
| `PUT` | `/api/v1/scripts/{id}` | 更新元数据 |
| `DELETE` | `/api/v1/scripts/{id}` | 软删除（`is_active=false`） |
| `POST` | `/api/v1/scripts/scan` | 扫描 NFS `scripts/` 目录，自动发现/注册/更新 |
| `GET` | `/api/v1/scripts/categories` | 分类列表 |

### 2.3 NFS 扫描逻辑

`POST /api/v1/scripts/scan` 行为：

```
1. 遍历 NFS scripts/{category}/{script_name}/v{x.y.z}/
2. 对每个版本目录：
   a. 发现新版本 → 计算 content_sha256 → 写入 Script 表
   b. 已有版本且 sha256 不变 → 跳过
   c. 已有版本但 sha256 变化 → 版本不可变！记录告警，跳过（不覆盖）
3. NFS 上已删除但 DB 中有记录的版本 → 标记 is_active=False
```

**原则**：同版本号内容不可变。内容变则版本号必须递增。由运维在 NFS 上创建新版本目录时保证。

### 2.4 Agent 同步

复用当前 ToolRegistry 的同步模式：

```
Agent 启动:
  GET /api/v1/scripts?is_active=true → 缓存到 SQLite (local_db)

Agent 心跳:
  上报 {"script_versions": {"push_bundle": "2.0.0", ...}}

平台比对:
  DB 最新 active 版本 vs Agent 上报版本 → 通知 scripts_outdated

Agent 收到过期通知:
  GET /api/v1/scripts → 增量更新 SQLite 缓存
```

### 2.5 Pipeline 中的引用格式

```json
{
  "step_id": "push_audio",
  "action": "script:push_bundle",
  "version": "2.0.0",
  "params": {
    "bundle_name": "audio_stability_v2",
    "target_dir": "/sdcard/test_resources/"
  },
  "timeout_seconds": 600
}
```

action 前缀 `script:` 区别于 `builtin:` 和 `tool:`。

---

## 组件 3：Workflow 级 Setup/Teardown Pipeline

### 3.1 数据模型变更

```python
# backend/models/workflow.py — WorkflowDefinition 新增字段

class WorkflowDefinition(Base):
    # 现有字段不变...
    setup_pipeline    = Column(JSONB, nullable=True)   # 设备级前置步骤
    teardown_pipeline = Column(JSONB, nullable=True)   # 设备级后置步骤
```

### 3.2 Dispatch 拼接逻辑

`dispatcher.py` — `dispatch_workflow()` 变更：

```python
# 对每个 JobInstance，将 workflow.setup_pipeline + template.pipeline_def
# + workflow.teardown_pipeline 拼成完整 pipeline

def _resolve_pipeline(setup: dict | None, task: dict, teardown: dict | None) -> dict:
    setup_steps   = (setup or {}).get("stages", {}).get("prepare", [])
    teardown_steps = (teardown or {}).get("stages", {}).get("post_process", [])

    task_stages = task.get("stages", {})

    return {
        "stages": {
            "prepare":      setup_steps,                                # 来自 Workflow
            "execute":      task_stages.get("execute", []),             # 来自 TaskTemplate
            "post_process": (task_stages.get("post_process", [])
                           + teardown_steps),                           # 合并
        }
    }
```

dispatch 时调用：

```python
resolved_pipeline = _resolve_pipeline(
    wf_def.setup_pipeline,
    template.pipeline_def,
    wf_def.teardown_pipeline,
)
job.pipeline_def = resolved_pipeline
```

**向后兼容**：`setup_pipeline` / `teardown_pipeline` 为 null 时拼接结果与当前行为一致。

### 3.3 TaskTemplate 语义变化

原有 TaskTemplate.pipeline_def 仍然完整保留。拼接发生在 dispatch 时，不修改 TaskTemplate 数据。

**建议约定**：配置 workfow-level setup 后，TaskTemplate 的 `prepare` 留空，`execute` 只写测试步骤。但引擎不强校验——即使 TaskTemplate 也有 prepare 步骤，拼接时不会覆盖（见 3.2 逻辑，task.prepare 不参与拼接）。

---

## 组件 4：幂等 Step 执行

### 4.1 StepResult 扩展

```python
@dataclass
class StepResult:
    success: bool = True
    exit_code: int = 0
    error_message: str = ""
    metrics: dict = field(default_factory=dict)
    skipped: bool = False         # 新增：标记步骤因幂等检查跳过
    skip_reason: str = ""         # 新增：跳过原因
```

### 4.2 关键 Action 幂等改造

#### connect_wifi

```python
def connect_wifi(ctx: StepContext) -> StepResult:
    ssid = ctx.params["ssid"]
    # 检查当前连接
    try:
        status = ctx.adb.shell(ctx.serial, "cmd -w wifi status", timeout=10)
        if ssid in _stdout(status):
            return StepResult(success=True, skipped=True,
                            skip_reason=f"Already connected to {ssid}")
    except Exception:
        pass  # 静默失败，继续执行连接
    # ... 执行实际连接
```

#### install_apk

```python
def install_apk(ctx: StepContext) -> StepResult:
    apk_path = ctx.params["apk_path"]
    pkg_name = ctx.params.get("pkg_name", "")
    required_version = ctx.params.get("required_version", "")

    if pkg_name:
        try:
            info = ctx.adb.shell(ctx.serial, f"dumpsys package {pkg_name} | grep versionName", timeout=10)
            if required_version and required_version in _stdout(info):
                return StepResult(success=True, skipped=True,
                                skip_reason=f"{pkg_name}=={required_version} already installed")
        except Exception:
            pass
    # ... 执行实际安装
```

#### push_resources（改造为 bundle 推送 + manifest 比对）

```python
def push_resources(ctx: StepContext) -> StepResult:
    bundle = ctx.params.get("bundle")             # NFS bundle 路径
    manifest_path = ctx.params.get("manifest")     # NFS manifest 路径
    remote_dir = ctx.params.get("remote_dir", "/sdcard/test_resources/")
    skip_if_match = ctx.params.get("skip_if_match", True)

    # 1. 加载 NFS manifest
    with open(manifest_path) as f:
        manifest = json.load(f)

    # 2. 检查设备端 manifest
    if skip_if_match:
        try:
            result = ctx.adb.shell(
                ctx.serial,
                f"sha256sum {remote_dir}/manifest.json 2>/dev/null",
                timeout=10,
            )
            remote_hash = _stdout(result).split()[0]
            if remote_hash == manifest["bundle_sha256"]:
                return StepResult(success=True, skipped=True,
                                skip_reason=f"Bundle {manifest['name']} already in sync")
        except Exception:
            pass

    # 3. Push bundle + 解压
    ctx.adb.push(ctx.serial, bundle, f"{remote_dir}/bundle.tar.gz")
    ctx.adb.shell(ctx.serial,
        f"cd {remote_dir} && tar xf bundle.tar.gz && sha256sum -c manifest.json && rm bundle.tar.gz",
        timeout=600,
    )

    return StepResult(success=True, metrics={
        "bundle": manifest["name"],
        "files": manifest["file_count"],
        "bytes": manifest["total_size_bytes"],
    })
```

### 4.3 Pipeline Engine 处理 skipped

`_run_step_with_retry_stages()` 中：

```python
if result.skipped:
    self._report_step_trace(step_id, stage, "SKIPPED", output=result.skip_reason)
    return True  # 跳过视为成功，不进入重试
```

---

## 组件 5：Pipeline Schema 更新

### 5.1 step action pattern 扩展

`pipeline_schema.json` step.action pattern 从：

```json
"pattern": "^(tool:\\d+|builtin:.+)$"
```

改为：

```json
"pattern": "^(tool:\\d+|builtin:.+|script:.+)$"
```

### 5.2 version required 条件扩展

当前 schema 仅对 `tool:` 强制要求 version。扩展为 `script:` 也必须带 version：

```json
"if": {
  "properties": { "action": { "pattern": "^(tool:|script:)" } },
  "required": ["action"]
},
"then": { "required": ["version"] }
```

### 5.3 validator 语义校验扩展

`pipeline_validator.py` 新增 `_validate_tool_references()` 同样处理 `script:` 引用，检查 `script` 表是否有对应 name+version 的激活记录。

---

## 组件 6：Pipeline Engine 脚本执行

### 6.1 新增 ScriptRegistry（Agent 侧）

```python
# backend/agent/registry/script_registry.py

@dataclass
class ScriptEntry:
    script_id: int
    name: str
    version: str
    script_type: str       # python | shell | bat
    nfs_path: str
    content_sha256: str

class ScriptRegistry:
    """Thread-safe script catalog. Same sync pattern as ToolRegistry."""
    def initialize(self) -> None: ...     # Server → SQLite cache
    def resolve(self, name: str, version: str) -> ScriptEntry: ...
    def resolve_latest(self, name: str) -> ScriptEntry: ...
```

### 6.2 PipelineEngine 新增 `_run_script_action`

```python
def _run_script_action(self, ctx: StepContext, step: dict) -> StepResult:
    name = step["action"].split(":", 1)[1]
    version = step.get("version", "")

    entry = self.script_registry.resolve(name, version)

    runners = {
        "shell":  ["bash", entry.nfs_path],
        "python": [sys.executable, entry.nfs_path],
        "bat":    ["cmd.exe", "/c", entry.nfs_path],  # Windows Agent
    }
    cmd = runners[entry.script_type]

    env = os.environ.copy()
    env.update({
        "STP_DEVICE_SERIAL":  ctx.serial,
        "STP_ADB_PATH":       self._adb_path,
        "STP_LOG_DIR":        ctx.log_dir or "",
        "STP_STEP_PARAMS":    json.dumps(ctx.params),
        "STP_NFS_ROOT":       self._nfs_root or "",
        "STP_JOB_ID":         str(ctx.job_id),
    })

    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True,
            timeout=step.get("timeout_seconds", 300),
            cwd=os.path.dirname(entry.nfs_path),
        )
        if proc.returncode == 0:
            output = json.loads(proc.stdout) if proc.stdout.strip() else {}
            return StepResult(
                success=True,
                metrics=output.get("metrics", {}),
            )
        else:
            return StepResult(
                success=False,
                exit_code=proc.returncode,
                error_message=proc.stderr[:2000],
            )
    except subprocess.TimeoutExpired:
        return StepResult(success=False, exit_code=124, error_message="script timeout")
```

### 6.3 契约规范

**脚本输入（环境变量）**：

| 变量 | 说明 |
|------|------|
| `STP_DEVICE_SERIAL` | 目标设备 ADB serial |
| `STP_ADB_PATH` | ADB 可执行文件路径 |
| `STP_LOG_DIR` | 日志输出目录 |
| `STP_STEP_PARAMS` | JSON 字符串，step 定义的 params |
| `STP_NFS_ROOT` | NFS 挂载根路径 |
| `STP_JOB_ID` | 当前 Job ID |

**脚本输出契约**：
- exit code 0 = 成功，非 0 = 失败（stderr 作为错误信息）
- stdout 可选输出 JSON：`{"metrics": {"files_pushed": 2000, ...}}`

---

## 组件 7：前端变更

### 7.1 WorkflowDefinitionEditPage 新增 Setup/Teardown 编辑器

在现有 TaskTemplate 列表上方，新增可折叠的 Setup Pipeline 和 Teardown Pipeline 编辑区：

```
┌─────────────────────────────────────────────────────┐
│  Workflow 基本信息 (name, description, threshold)    │
├─────────────────────────────────────────────────────┤
│  ▼ 设备前置步骤 (Setup Pipeline)        [展开/收起]  │
│     ┌─ StagesPipelineEditor (只读/编辑) ──────────┐ │
│     │ prepare: [connect_wifi] [install_apk] [...]  │ │
│     │ execute: (空，setup 不使用 execute)            │ │
│     └──────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────┤
│  Task Templates                           [+ 新增]  │
│  ┌─ TaskTemplate 1: monkey_test ──────────────────┐ │
│  └─ TaskTemplate 2: performance_test ─────────────┘ │
├─────────────────────────────────────────────────────┤
│  ▼ 设备后置步骤 (Teardown Pipeline)     [展开/收起]  │
│     ┌─ StagesPipelineEditor ──────────────────────┐ │
│     │ post_process: [uninstall_app] [cleanup]      │ │
│     └──────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 7.2 TypeScript 类型扩展

```typescript
// types.ts — WorkflowDefinition 扩展
export interface WorkflowDefinition {
  // ... 现有字段
  setup_pipeline?: PipelineDef | null;
  teardown_pipeline?: PipelineDef | null;
}

// PipelineDef action pattern 扩展
// action: "builtin:xxx" | "tool:42" | "script:push_bundle"
```

### 7.3 StagesPipelineEditor 支持 script action

当用户选择 action 类型为 "Script" 时：
1. 从 `GET /api/v1/scripts?is_active=true` 拉取可用脚本列表
2. 按 category 分组展示（类似现有 builtin optgroup）
3. 选定脚本后，按 `param_schema` 渲染 DynamicToolForm（不再裸 JSON）
4. 自动填充 version 为最新 active 版本

### 7.4 新增 Script 管理页面（P1，非阻塞）

一个独立管理页 `/admin/scripts`：
- 脚本列表（按 category 分组）
- 显示 name / version / script_type / is_active / content_sha256 前8位
- 支持手动创建、编辑、deactivate
- "重新扫描" 按钮触发 POST /api/v1/scripts/scan

---

## 实施顺序与文件清单

### Phase 1 — 数据模型 + API（后端基础）

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/models/script.py` | **新建** | Script ORM |
| `backend/models/workflow.py` | **修改** | WorkflowDefinition 新增 setup_pipeline/teardown_pipeline 字段 |
| `backend/api/routes/scripts.py` | **新建** | Script CRUD + scan 端点 |
| `backend/api/routes/orchestration.py` | **修改** | WorkflowDefinition schema 扩展 |
| `backend/main.py` | **修改** | 注册 scripts router |
| Alembic migration | **新建** | script 表 + workflow_definition 新增字段 |

### Phase 2 — Agent 执行层

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/agent/registry/script_registry.py` | **新建** | Agent 侧脚本注册表（同步+缓存） |
| `backend/agent/registry/local_db.py` | **修改** | 新增 script 缓存表 |
| `backend/agent/pipeline_engine.py` | **修改** | 新增 `_run_script_action`，`_resolve_action_stages` 新增 script: 分支 |
| `backend/agent/actions/device_actions.py` | **修改** | connect_wifi/install_apk/push_resources 幂等改造 |
| `backend/agent/heartbeat.py` | **修改** | 心跳上报 script_versions |
| `backend/core/pipeline_validator.py` | **修改** | schema pattern 扩展 + script 引用校验 |
| `backend/schemas/pipeline_schema.json` | **修改** | action pattern 扩展 |

### Phase 3 — Dispatch 拼接逻辑

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/services/dispatcher.py` | **修改** | `_resolve_pipeline()` 拼接 setup + task + teardown |

### Phase 4 — 后端 API 扩展

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/api/routes/heartbeat.py` | **修改** | 心跳响应新增 scripts_outdated 检测 |
| `backend/api/schemas.py` | **修改** | WorkflowDefinition schema 新增 setup/teardown 字段 |

### Phase 5 — 前端

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/utils/api/types.ts` | **修改** | PipelineDef action 扩展，WorkflowDefinition 扩展 |
| `frontend/src/pages/orchestration/WorkflowDefinitionEditPage.tsx` | **修改** | 新增 Setup/Teardown 编辑区 |
| `frontend/src/components/pipeline/StagesPipelineEditor.tsx` | **修改** | 新增 script action 类型支持 |
| `frontend/src/utils/api.ts` | **修改** | 新增 api.scripts 命名空间 |

---

## 验证方案

### 后端验证

1. Alembic migration 执行无错误
2. `POST /api/v1/scripts/scan` 扫描 NFS 测试目录，验证自动注册
3. `GET /api/v1/scripts?is_active=true` 返回正确数据
4. Dispatch workflow with setup_pipeline → JobInstance.pipeline_def 正确拼接
5. `validate_pipeline_def()` 接受 `script:xxx` action 格式
6. 心跳返回 `scripts_outdated` 标志

### Agent 验证

1. ScriptRegistry.initialize() 加载脚本元数据
2. `_run_script_action` 通过 subprocess 正确执行 shell/python 脚本
3. 环境变量正确传递到子进程
4. 幂等 step：第二次执行同设备同 bundle → 返回 skipped
5. 脚本超时正确 kill 子进程

### 端到端

1. 创建 WorkflowDefinition（含 setup_pipeline），添加 TaskTemplate（只有 execute）
2. Dispatch 到设备 → Agent claim → setup 执行 → task 执行
3. 设备上资源已存在时 setup step 输出 SKIPPED
4. 第二次 dispatch 到同设备 → setup 全部 SKIPPED，仅 execute 实际执行
