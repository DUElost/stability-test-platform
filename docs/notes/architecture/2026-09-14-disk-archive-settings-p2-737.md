# ADR-0042 P2（#2 磁盘与日志归档域）：DiskArchiveSettings 迁移（#737）

Status: implemented
Class: architecture

## Decision

按 D2 候选清单第二项，把 agent 侧**磁盘监控与日志归档**域的 8 个旋钮收敛到
`backend/agent/settings.py::DiskArchiveSettings`（自包含，沿用 P1 的 agent 侧形态）：

| 组 | 旋钮（env → 字段） |
|---|---|
| 宽容组（3） | `STP_HDD_SPILL_CRITICAL_PCT` · `STP_HDD_SPILL_CRITICAL_BATCH` · `STP_HDD_SPILL_CATCHUP_INTERVAL` |
| 严格组（5） | `STP_LOCAL_DISK_MONITOR_INTERVAL_SECONDS` · `STP_LOCAL_DISK_SPILL_THRESHOLD` · `STP_LOCAL_DISK_SPILL_TARGET` · `STP_LOG_ARCHIVE_INTERVAL_SECONDS` · `STP_LOG_ARCHIVE_GRACE_SECONDS` |

### 两个关键设计点

1. **失败形态逐旋钮对齐（等价性优先）**：宽容组迁移前是 #1710 的宽容解析
   （「非法值不得拖垮 Agent 启动」——只告警并回落默认），**必须保留**：以
   `field_validator(mode="before")` 承载同一语义与同一告警文案；严格组迁移前是
   `float(os.getenv(...))` 直转，保持严格类型（`ValidationError` 即等价的启动失败面）。
   ——**「统一成一种失败形态」是行为变更，未在本单做**（Revisit）。
2. **类属性 → 惰性 property**：`HddSpillMonitor` 的三个旋钮原是**类体属性**
   （import 期求值），改为 `@property` 读 Settings——既满足 D4（不在 import 期读 env），
   又保持既有读取点（`mon._CRITICAL_USAGE_PCT` 等）与测试不变。

配套：`reset_agent_settings_caches()` 按 P1 Note 的预告扩为「清 agent 侧**全部**域缓存」
（lease + disk），hot-update 的 `reload_config` 路径无需再改。

### 过程记录（三处发现，均由机制当场暴露）

**（0）第二个「行扫描盲区」被 Settings 暴露**：`STP_HDD_SPILL_CRITICAL_BATCH` 的迁移前读取是
**多行 helper 调用**（`_parse_positive_int_env(\n "STP_HDD_SPILL_CRITICAL_BATCH", 100,\n)`），
行级清单扫描一直漏掉它（清单里没有这个 env）；Settings（AST 扫描）把它暴露出来后，
按二选一补登记进 `backend/agent/.env.example`（清单 211 → **212** 名）。
——与 P2a 的 `POST_COMPLETION_MAX_DEFER_SECONDS` 同类，说明 **Settings 化在收敛的同时
持续在补清单死角**。

**（1）agent 侧又踩了绝对导入**：首版在 `local_disk_monitor.py` 写 `from backend.agent.settings import …`，
被既有守卫测试 `test_agent_runtime_imports_without_backend_package` 当场抓住
（部署布局是 `agent.*`、无 `backend` 包）→ 改相对导入 `from .settings import …`。
**C1 约束（agent 侧自包含 + 相对导入）必须每单复查。**

### 过程记录：一个被「宽容语义」掩盖的真实缺陷

首版字段名漏了 `stp_` 前缀（env 名是 `STP_LOCAL_DISK_*`），pydantic 因此找不到对应
env——**宽容组「回落默认」让 6 个字段的失配看起来像通过**，只有「合法值应生效」的
用例暴露了它（`assert 80.0 == 65.0`）。修法是把字段名改为 env 名小写全称（含 `stp_`），
并复核了「字段名 = env 名小写」这条 D3 规则在每个字段上成立。
教训：**宽容回落会掩盖 env 绑定失配**，因此每个域的测试必须同时覆盖
「非法值回落」与「合法值生效」两侧。

## Alternatives

- **宽容组也改严格**：否决（本单）——直接回退 #1710 的修复目标（非法值拖垮启动）；
- **保留 local_disk_monitor 内的三个解析助手**：否决——默认值与解析逻辑必须单点，
  否则 Settings 与助手会各自漂移；
- **把 `_MAX_SPILL_PER_CYCLE = 20` 一并进 Settings**：否决——它不是 env 旋钮
  （纯代码常量，无 env 名），进表会污染「字段名 = env 名」的契约。

## Verification

- 新增 `test_agent_settings_disk_archive.py`：默认值逐一对照 · **宽容组 12 例**
  （bogus/越界/非有限/`30s`/非法 batch → 默认；合法值 `97.5`/`250`/`45` 生效）·
  **严格组 5 例**（非法 → `ValidationError`）· env 覆盖 + 缓存语义 · `.env` 负向 ·
  **监控类 property 随 Settings 变化**；
- 解析器测试随迁（`test_local_disk_monitor.py` 的 `_parse_spill_catchup_interval`
  用例 → Settings 语义，参数表不变）；
- `pytest backend/agent/tests/` → **1952 passed**；`pytest tests/`（根）→ **571 passed**
  （env_inventory **212** 名一致）；`check:quick` → **10 gates 全绿**；gov-surface（S1–S13、S5x）全绿；
- 迁移后这两个文件的裸 `os.getenv` 归零（磁盘/归档相关 0 处）。

## Revisit

- **失败形态统一**：宽容组 vs 严格组并存是迁移期的等价性妥协；是否统一为
  「全部宽容（warn+default）」或「全部严格（启动失败）」需独立裁决（涉及 #1710 的边界）；
- **agent 侧 reset 入口已扩**：新增 agent 域时，其 `reset_*` 必须挂进
  `reset_agent_settings_caches()`（否则 hot-update 后旧缓存吞新值）；
- P2 其余候选：agent 心跳/协调/注册 → realtime 多实例 → 通知与报告 → watcher → SAQ。
