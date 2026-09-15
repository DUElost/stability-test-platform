# ADR-0042 P2（#3 agent 心跳/协调/注册域）：HeartbeatSettings + RegistrationSettings 迁移（#737）

Status: implemented
Class: architecture

## Decision

按 D2 候选清单第三项，把 agent 侧**心跳/协调/注册**域的 9 个旋钮收敛到
`backend/agent/settings.py`（自包含，沿用 P1/P2 的 agent 侧形态），按模块内聚拆成两个域类：

| 类 | 旋钮（env → 字段） | 消费方 |
|---|---|---|
| `HeartbeatSettings`（6） | `COORDINATOR_HEARTBEAT_INTERVAL` · `COORDINATOR_MAX_PLAN_RUN_HOSTS` | `coordinator.py`（构造时取值） |
| | `STP_HEARTBEAT_INTERVAL_MIN` · `STP_HEARTBEAT_INTERVAL_MAX` · `STP_ADB_REPAIR_COOLDOWN_SECONDS` | `heartbeat_thread.py`（构造时取值） |
| | `STP_ADB_AUTO_REPAIR` | `heartbeat_thread.py`（**tick 时**取值） |
| `RegistrationSettings`（3） | `AUTO_REGISTER_HOST` · `AUTO_REGISTER_MAX_RETRIES` · `AUTO_REGISTER_RETRY_DELAY` | `main.py` 启动注册块 |

### 三个关键设计点

1. **数值旋钮全部保持严格类型**：迁移前都是 `float(...)`/`int(...)` 直转（非法值启动即
   失败）→ 字段用严格类型，`ValidationError`（`get_*_settings()` 抛出点）即等价的启动
   失败面。本域**没有** #1710 式宽容旋钮，故不引 `mode="before"` validator。
2. **两个字符串旋钮走派生值，保留精确比较语义**：`STP_ADB_AUTO_REPAIR` 迁移前是
   `== "1"`（**不 strip、不认 `true/yes`**），`AUTO_REGISTER_HOST` 是
   `.lower() == "true"`（认大小写、**不认 `1/yes/on`**）。字段保持 `str` 原值透传 +
   `@property adb_auto_repair_enabled` / `auto_register_enabled`——避免 pydantic `bool`
   解析顺手放宽语义（`bool` 会认 `1/yes/on/True`）。
3. **首个「仅告警」跨字段检查**：`min > max` 时迁移前是**静默**把控制面 hint 恒压到
   `min`（`max(min, min(max, hint))` 的退化）——本单加
   `model_validator(mode="after")` **只补一条 WARNING，不改值、不抛错**
   （行为零变更，纯可观测性；这是 D2 判据 3 意义上的跨字段关系首次落地）。

配套：`reset_agent_settings_caches()` 继续扩（lease + disk + heartbeat + registration），
hot-update 的 `reload_config` 路径无需再改。

### 边界决定（本单不收，留痕防「漏迁」误判）

- **`POLL_INTERVAL`（默认 5）**：既是 heartbeat_thread 的 `poll_interval`，也是主循环
  claim 等待（`main.py` `_shutdown_event.wait(poll_interval)`）——**跨域**，留给后续
  「agent 主循环」域；
- **`STP_RECOVERY_SYNC_INTERVAL_SECONDS`**：已有 #1710 自带的宽容解析
  （`_coerce_recovery_interval`，非法回落 60 再夹下限 5），且属「恢复同步」语义，
  留给对应域单独处理（其失败形态是宽容，需按 #1710 口径迁）；
- **`HOST_ID`（身份）/ `AGENT_SECRET`（凭据）**：D5/C2 边界，继续裸读；
- **`STP_HEARTBEAT_INTERVAL_MIN/MAX` 的控制面读取**（`backend/api/routes/heartbeat.py`，
  默认 **15/60**）：与 agent 侧（默认 **10/120**）是**不同进程、各自 `.env`**，
  两处默认值差异是既有设计（控制面给建议区间、agent 侧钳制区间更宽），
  且分别登记在 `backend/.env.example` 与 `backend/agent/.env.example`。
  本单只迁 agent 侧读取，**控制面侧不动**（见 Revisit）。

### 过程记录

- **C1（agent 自包含 + 相对导入）本单两处应用**：`coordinator.py` / `heartbeat_thread.py`
  用 `from .settings import …`；同时删掉了两文件迁移后不再使用的 `import os`
  （ruff 无 F401 残留）。
- **tick 时读取的语义窄化是显式的**：`STP_ADB_AUTO_REPAIR` 迁移前每次 tick 现读 env，
  迁移后读惰性缓存（进程内 env 不会变；hot-update 走 `reload_config` →
  `reset_agent_settings_caches()` 也覆盖）。测试因此新增 `heartbeat_env` fixture
  （与 `disk_env` 同形：写 env 必须伴随 reset，否则读到旧缓存）。
- **首版踩了「失败面扩大」并当场修正**：首版在 `main()` 的 HOST_ID 加载点**之前**
  无条件调用 `get_registration_settings()`——迁移前 `AUTO_REGISTER_MAX_RETRIES`
  只在 `host_id is None`（自动注册模式）才被 `int(...)` 解析，即 HOST_ID 正常的机器
  永远不会因该旋钮非法而启动失败；无条件构造会让它们被用不到的旋钮拖垮。
  修法：两个分支内各自惰性取值（`except` 分支取 flag；`host_id is None` 分支取
  retries/delay），并补 `test_registration_settings_read_stays_deferred` 静态契约
  （main.py 源文本顺序 + 取值点计数，沿用 #784 的静态契约测试形态）。
  ——**迁移的等价性包含「取值时机」，不只是「取值结果」**。

## Alternatives

- **合并成一个 `CoordinationSettings`（9 字段）**：否决——注册是启动期一次性动作、
  心跳是常驻线程行为，两类的消费方与取值时机不同；D1 允许按域拆分，拆开更贴模块边界。
- **两个 flag 旋钮改成 `bool` 字段**：否决——会放宽语义（见设计点 2），
  与 #2002 `cookie_secure_enabled` 的处理保持一致。
- **`STP_ADB_AUTO_REPAIR` / `STP_ADB_REPAIR_COOLDOWN_SECONDS` 留给未来「ADB 设备域」**：
  否决（本单）——两者只被 heartbeat_thread 的 tick 消费；若未来真有 ADB 域迁移，
  随该单移动即可（已在 Note 留痕）。
- **把 `min > max` 改成夹正或抛错**：否决——属行为变更，本单只做等价迁移 + 告警。

## Verification

- 新增 `backend/agent/tests/test_agent_settings_heartbeat.py`（32 例）：默认值逐一对照
  （含类型不漂移）· 严格组 7 例（非法 → `ValidationError`）· **`STP_ADB_AUTO_REPAIR` 8 例**
  （仅 `1` 为真；`true/TRUE/01/ 1/1 /空串` 全假，原值透传）· **`AUTO_REGISTER_HOST` 9 例**
  （`true/TRUE/True` 真；`1/yes/on/false/ true/空串` 假）· `min > max` 只告警不改值 ·
  env 覆盖 + 缓存 + reset 语义 · `.env` 负向 · **`HeartbeatThread` /
  `HostRunCoordinator` 构造值随 Settings 变化** · **注册域取值点保持惰性**（静态契约）；
- 既有 `test_heartbeat_thread_adb_server_conflict.py` 改用 `heartbeat_env` fixture，
  并补**负数例**：`STP_ADB_AUTO_REPAIR=true` 不触发修复（防 Settings 化放宽语义）；
- `backend/agent/tests/` → **1985 passed**；`tests/`（根）→ **591 passed**；
  `env_inventory --check` → 212 名一致（9 行的来源列刷新为 `backend/agent/settings.py`，
  默认值列全部不变）；`check:quick` → 10 gates 全绿；gov-surface（S1–S13、S5x）全绿；
- 迁移后三文件中心跳/注册相关裸 `os.getenv` 归零（`coordinator.py` 全部；
  `heartbeat_thread.py` 全部；`main.py` 仅剩注册块外的其它域旋钮）。

## Revisit

- **控制面侧的同名旋钮**（`STP_HEARTBEAT_INTERVAL_MIN/MAX` 默认 15/60 +
  `STP_HEARTBEAT_INTERVAL_BASE`）是否并入某个控制面域 Settings（或反过来让 agent 侧
  默认与之一致）——是产品/运维口径决定，不是迁移机制决定；
- **tick 时读取点的缓存语义**：若未来出现运行期改 env 的合法路径（当前只有
  `reload_config`），需改为逐 tick 无缓存取值；
- P2 其余候选：realtime 多实例（#1737 已在做）→ 通知与报告 → watcher → SAQ。
