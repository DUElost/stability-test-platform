# ADR-0042 P1 试点（agent 侧）：租约续期域 Settings + hot-update reload 钩子（#737）

Status: implemented
Class: architecture

## Decision

按 [ADR-0042](../../adr/ADR-0042-settings-convergence-and-bare-read-boundary.md) 的 P1 试点，
把 **agent 侧租约续期域**（`backend/agent/lease_renewer.py`）的 5 个旋钮收敛到
自包含的分域 Settings，并补齐 hot-update 的 reload 钩子。

- 新增 `backend/agent/settings.py`：`LeaseSettings`（5 个字段：`agent_post_retries`=3、
  `agent_post_retry_base_delay`=1、`agent_lock_renewal_interval`=60、
  `agent_lease_extend_batch_chunk`=100、`agent_lease_ttl`=600）+ `get_lease_settings()`
  + `reset_agent_settings_caches()`；
- `lease_renewer.py`：删除 5 处 `os.getenv`（含 `_BACKEND_LEASE_TTL` 常量，其默认值
  迁入 Settings 字段并保留来源注释），**钳制语义（`max(..., 1)`）保持在调用点**以保
  行为等价；`AGENT_SECRET` 仍裸读（凭据不入 Settings 表，ADR-0042 D5 边界）；
- `main.py`：`reload_config` 分支在 `_reload_runtime_env()` 之后调用
  `reset_agent_settings_caches()`——hot-update 改写 `.env` → 重读 → 清缓存 → 新值
  对续租器生效；
- `backend/agent/requirements.txt`：补 `pydantic-settings>=2.10,<3.0`（目标机安装通道
  已存在：host_updater 检测 requirements SHA 变化后 `pip install -r`（带
  `STP_AGENT_PIP_INDEX_URL`），**装失败则不重启**——fail-safe 成立）。

### 部署布局约束（本单踩到并修正）

agent 代码以**双包布局**运行：部署时是 `agent.*`（`PYTHONPATH=<repo>/backend`，**没有
`backend.core`**），开发/CI 是 `backend.agent.*`。因此：

- agent 侧 Settings **自包含**（只 import `pydantic_settings`，不 import 后端包）；
- 跨模块引用用**相对导入**（`from .settings import ...`），`main.py` 的 barrel 按既有
  惯例登记进它的 try/except 双导入块。
  首版我在 `main.py` 顶部写了绝对导入 `from backend.agent.settings import ...`，
  被既有守卫测试 `test_agent_runtime_imports_without_backend_package` 当场抓住
  （`ModuleNotFoundError: No module named 'backend'`）——该测试正是为此类错误设的防线。

## Alternatives

- **agent 复用 `backend/core/settings/base.py`**：否决——部署布局不含 `backend.core`；
  自包含 + 相对导入是唯一可行形态；
- **把 `AGENT_SECRET` 也放进 Settings（SecretStr）**：否决——凭据不是可调旋钮，
  且 SecretStr 会牵动调用点类型（本单只做旋钮，边界写进模块 docstring）；
- **钳制改由 Field(ge=1) 表达**：暂不做——迁移前的钳制在调用点，统一会改变边界行为
  （如 0/负值从「钳到 1」变为「启动报错」），留作 ADR Revisit 的独立裁决；
- **不补 requirements**：否决——目标机 `agent/requirements.txt` 是唯一安装源，
  漏补会让 hot-update 后 agent 起不来（pip 阶段失败虽会阻止重启，但等于卡住更新）。

## Verification

- `pytest backend/agent/tests/test_agent_settings_lease.py`（新增 7 例）：
  默认值/类型逐一对照迁移前、env 覆盖需 reset、非法值 `ValidationError`、
  **`.env` 文件单独存在不生效**、**reload 闭环**（`main._reload_runtime_env(tmp env)`
  → 新值可见）、`reset` 已接进 `reload_config` 分支的静态契约断言；
- `pytest backend/agent/tests/` → **1931 passed**（含既有守卫
  `test_agent_runtime_imports_without_backend_package` 通过）；
- 迁移后 `backend/agent/lease_renewer.py` 仅剩 1 处 `os.getenv`（`AGENT_SECRET`，有意保留）；
- `pytest tests/`（根）→ **532 passed**（含 env_inventory 211 名一致）；
- `run_gates.py check:quick` → **10 gates 全绿**；
- `ruff` 通过。

## Revisit

- **P2 剩余**：scheduler 域四个 reconciler 的 7 个批处理旋钮；agent 其余域
  （watcher/磁盘/注册等）按 ADR D2 判据逐个评估；
- **agent venv 未锁 hash**：目标机按 `agent/requirements.txt` 区间安装（非 lock），
  跨主机版本可能漂移——属既有形态，是否引入 agent 侧 lock 另议；
- **钳制 vs 校验**：如上；
- **reload 覆盖面**：当前只清租约域缓存；将来 agent 侧多域时，`reset_agent_settings_caches()`
  应扩成「清全部 agent Settings」——届时改名/扩展并在 Note 记录。
