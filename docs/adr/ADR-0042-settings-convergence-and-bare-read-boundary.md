# ADR-0042：配置读取收敛——分域 pydantic-settings 与裸读取边界

- 状态：**Accepted**（v1.1：P1 试点完成并回填。2026-09-14 裁决：引入 pydantic-settings；按 D2 判据分域迁移，不满足判据者保持裸读）
- 版本记录：
  - v1.1（2026-09-14）：**P1 试点完成并回填**——依赖（#1970）+ D6 门禁（#1971）+ 控制面调度域（#1977，21 旋钮）+ agent 租约域（#1984，5 旋钮 + hot-update reload 钩子）。结论：D1–D4/D6 按裁决落地、等价性测试全绿；新增两条实作约束（见 §P1 试点结论）。P2 待启动。
  - v1.0（2026-09-14）：用户委托本会话裁决——**引入**；载体取 D1 分域 Settings（否决方案 A 薄封装、方案 C 维持现状）；**附加硬约束**：Settings 仅读 `os.environ`（`env_file=None`），不得引入第二个 dotenv 加载器（`env_source.py` 仍是来源与优先级的唯一契约）；D6 门禁扩展为 P1 试点的**前置条件**。**转 Accepted**。
  - v0.1（2026-09-14 初版，由 #737 收口后的 deferred 项触发；现状盘点与分域判据见正文）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-09-14
- 决策者：本需求用户（2026-09-14 委托本会话裁决；裁决依据见「备选方案与权衡」与 v1.0 版本记录）
- 标签：配置, 环境变量, pydantic-settings, 可维护性, 依赖治理, #737
- 关联：[#737](https://github.com/DUElost/stability-test-platform/issues/737)（配置黑盒与死打点治理；本 ADR 承接其 deferred「向统一配置管理类收敛」）、[`docs/development/environment-variables.md`](../development/environment-variables.md)（读取清单与裁决口径）、[`backend/core/env_source.py`](../../backend/core/env_source.py)（env 来源与优先级唯一契约）、[`backend/core/job_timeout_config.py`](../../backend/core/job_timeout_config.py)（既有集中默认值先例）、[`docs/development/dependencies-and-quality.md`](../development/dependencies-and-quality.md)（依赖与 lock 流程）

## 背景

### 现状（2026-09-14 实测）

- **读取点**：`backend/**`（不含测试与 `agent/scripts/`）有 **325 处** `os.getenv` /
  `os.environ.get`；分布：agent 105（含 aee 14、watcher 10）、services 55、core 41、
  `backend/scripts/` 35、scheduler 30、api/routes 20、main.py 16、realtime 14、tasks 9。
- **import-time 固化**：其中 **70 处**是模块级常量（`NAME = int(os.getenv(...))`），
  进程启动即定型；agent 侧还叠加 hot-update 行级改写 `.env` 的运行时语义。
- **默认值与类型转换分散**：`int(os.getenv("X", "30"))` 形态遍布各域；各域自建 helper
  （`job_timeout_config._int_env`、`cors._csv_env`、`agent/local_disk_monitor._parse_pct_env`），
  同语义不同实现；非法值要么静默回落默认、要么在调用深处才抛错。
- **可发现性已解决**：#737 收口后 210 个读取名全部可查（91 个登记进运维模板，
  19 个内部声明，其余见附录），并有 `env-example-parity` 与 `env-inventory` 门禁
  强制「登记 ∪ 声明」二选一。**本 ADR 不解决「看不见」，只解决「没有类型/校验/单点默认值」。**
- **来源契约已存在**：`backend/core/env_source.py` 定义优先级（进程 env > `.env.backend`
  > `backend/.env`；`TESTING=1` 跳过），任何收敛层都必须尊重它，不得引入第二套来源解析。
- **依赖现状**：`pydantic-settings` **未安装**（不在 requirements 源/lock 中）；新增依赖
  需走源文件 + `requirements.lock`（`--require-hashes`）+ `tests/test_requirements_lock.py` 校验。

### 问题（本质）

裸 `os.getenv` 的真正代价不是「分散」，而是**没有单点**：类型、默认值、非法值行为、
别名兼容（如 `RUN_HEARTBEAT_TIMEOUT_SECONDS` 为 `RUNNING_...` 的兼容别名）散落在调用点，
无法评审、无法复用、无法在一次变更中看到全貌；测试只能逐个 `monkeypatch.setenv`。

## 决策

**D1 引入 `pydantic-settings` 作为分域 Settings 基础设施**，落点 `backend/core/settings/`，
按域建模（如 `db.py` / `auth.py` / `scheduler.py` / `notify.py` / `console.py` / `storage.py`），
每域一个 `BaseSettings` 子类。**不建全仓单一巨型模型。**
**来源约束（v1.0 裁决附加）**：Settings 必须只读 `os.environ`（`env_file=None`，不启用
pydantic-settings 自带的 dotenv 加载）——`.env` 来源与优先级仍由 `env_source.py` 独家决定，
禁止出现第二个来源解析器。

**D2 迁移判据（不为迁移而迁移）**：某域满足任一条件才迁移，否则**保持裸读**
（继续受清单门禁约束）：

1. 该域旋钮 ≥ 3 个；
2. 类型转换 / 默认值在同一域内重复 ≥ 2 处；
3. 需要跨字段校验或派生值（例：TTL 与续期间隔的约束关系）。

**D3 环境变量名不变**：字段通过 `validation_alias` 绑定**既有** env 名与别名，
不引入新前缀、不重命名、不改变 `.env` 文件语义；运维面零感知。

**D4 惰性访问**：提供 `get_settings()`（`lru_cache` + `cache_clear()`），禁止 import 时固化；
迁移域的模块级常量改为惰性取值。Agent 侧提供与 hot-update 对齐的 `reload_settings()`
（清缓存重建），保证 `.env` 行级改写后重读生效；后端进程内不热更（与现状一致）。
**验收（v1.0 裁决附加）**：P1 试点必须含「Settings 不读 `.env` 文件」的负向用例
（仅设文件、不设进程 env → 不得生效），防止来源契约被悄悄旁路。

**D5 非目标（明确排除）**：

- 注入型 / 协议键（`STP_STEP_PARAMS`、`STP_DEVICE_SERIAL`、`ENV_OVERRIDES_B64`、
  `SUDO_UID/GID` 等）——属运行协议，不属配置；
- `backend/scripts/**`（运维/一次性脚本）与 `backend/agent/scripts/**`（ADR-0020 版本化
  脚本目录，环境契约自管）；
- 不改变 `env_source.py` 的来源与优先级契约。

**D6 清单联动（防可见性回退）**：`tools/dev/env_inventory.py` 扩展解析 Settings 字段
与 `validation_alias`，保证 Settings 化后读取名仍进清单与门禁；否则「收敛」会变成
新的不可见面（这是本 ADR 的硬约束，与 #737 的成果互锁）。

**D7 分阶段**：

- **P1 试点**：选 1 个控制面域 + 1 个 agent 域（建议 `scheduler`（旋钮多、无协议语义）
  与 `agent/lease_renewer`（验证 reload 与 `.env` 改写路径））；产出：依赖接入 +
  等价性测试 + 门禁扩展（D6）；
- **P2 逐域迁移**：按 D2 判据逐域 PR，每域带等价性测试；
- **P3 收口**：裸读仅剩 D5 非目标项；清单门禁把 Settings 字段纳入统计口径。

## 备选方案与权衡

- **A 薄封装 accessor（`config.get_int("X", default=…)`），不引入新依赖**：改动最小、
  风险最低、可中心化默认值；但拿不到模型校验、字段文档一体化与 IDE 补全，
  校验仍需自研。**若裁定「不新增依赖」，退而采用本方案**（本 ADR 的 D2/D3/D4/D6 判据与
  门禁联动仍适用，只把 D1 的载体换成薄封装）。
- **B 全仓单一 Settings 模型**：单点最彻底，但 325 处读取面一次性卷入，
  import-time 与 agent/scripts 混杂风险高，回退困难——否决。
- **C 维持现状（仅靠清单 + 门禁）**：黑盒问题已解，#737 的收益已落袋；
  但类型/校验/默认值仍分散，测试仍需逐个 setenv——作为「不投入」基线保留，
  需在裁决时明确接受其代价。
- **D 自研 dataclass + 手写校验**：不引依赖，但重复造轮子、与 pydantic v2 生态脱节——否决。

## 影响

- **依赖面**：新增 `pydantic-settings`（源 + lock + hash；镜像体积 +1 包）；`pydantic` v2
  已在依赖内，版本兼容风险低（需在 PR 中核对下限）。
- **代码面**：迁移域减少裸读；未迁移域不受影响（判据控制，无「大爆炸」窗口）。
- **测试面**：迁移域的测试从 `monkeypatch.setenv` 逐步转为 `cache_clear()` + 构造 Settings
  （两种模式在 P1 试点里给出范例与迁移说明）。
- **运维面**：env 名与 `.env` 语义不变，运维零感知；hot-update 路径需在 P1 用真机/仿真
  验证 reload 生效（与 `ENV_PATH_KEYS_B64` 选定键的改写路径对齐）。
- **回退**：单域可回退（Settings 使用点还原为裸读；门禁与清单不变），不产生跨域耦合。

## P1 试点结论（v1.1 回填，2026-09-14）

### 落地清单

| 步骤 | PR | 覆盖 |
|---|---|---|
| 依赖（源 + 两份 lock） | #1970 | `pydantic-settings==2.15.0`；净新增 1 包、无既有 pin 漂移；agent 侧 `backend/agent/requirements.txt` 同补 |
| D6 门禁（Settings 可见性） | #1971 | AST 解析 Settings 字段/别名，清单与二选一裁决自动适用；首版「任意 model_config 即 Settings」误收 130+ 模型字段，被门禁当场暴露并收紧 |
| 控制面：调度域 | #1977 | `backend/core/settings/scheduler.py`（21 字段）；迁移 `app_scheduler`/`recycler`/`cron_scheduler`，并去重两处重复旋钮；测试语义由常量打桩改为 `scheduler_env` fixture |
| agent：租约域 | #1984 | `backend/agent/settings.py`（5 字段，自包含）；迁移 `lease_renewer`；`reload_config` 路径补清缓存钩子 |

### 结论（验证到的机制点）

1. **惰性 + 缓存语义可用**：`get_*_settings()` + `reset_*_settings_cache()` 覆盖
   「运行期改 env / hot-update 重读」两类场景；控制面不热更（现状不变），agent 侧
   `reload_config` 闭环已用 `_reload_runtime_env(tmp)` 实测；
2. **等价性可证**：两域均以「字段默认值/类型逐一对照迁移前字面量」的测试锁定（21 + 5 项），
   钳制语义（`max(...,1)`）保留在调用点，未引入行为变化；
3. **来源契约未被旁路**：两域各含「只写 `.env` 文件、不设进程 env → 不生效」的负向用例；
4. **门禁联动有效（D6 的价值实证）**：Settings 化让一度被行级扫描漏掉的
   `POST_COMPLETION_MAX_DEFER_SECONDS`（跨行 `os.getenv`）暴露并补登记（清单 210 → 211 名）——
   收敛没有制造新的不可见面，反而补了一个死角。

### 两条实作约束（v1.1 新增，后续域必须遵守）

- **C1（agent 侧）双包布局**：agent 以 `agent.*`（部署，无 `backend.core`）与
  `backend.agent.*`（开发）两种包名运行。agent 侧 Settings **必须自包含**
  （不 import 后端包、跨模块用相对导入；`main.py` barrel 按其既有 try/except 双导入块登记）。
  违反例：首版绝对导入被既有守卫测试 `test_agent_runtime_imports_without_backend_package` 当场抓住；
- **C2 凭据不入 Settings 表**：`AGENT_SECRET` 等凭据保持裸读；Settings 只承载可调旋钮
  （D5 边界的细化口径）。

### P2 范围（按 D2 判据执行，不扩不缩）

- 控制面：`counter/signal_link/plan_chain/precheck` 四个 reconciler 的 7 个批处理旋钮；
- agent：其余域（watcher/磁盘/注册等）逐个按 D2 判据评估——满足才迁，不满足保持裸读
  （仍受 env_inventory 门禁约束）。

## 落地与后续动作

1. 本 ADR 裁决（接受 / 接受但改载体为方案 A / 不投入）；
2. 接受后：依赖 PR（源 + lock）→ P1 试点 PR（域迁移 + 等价性测试）→ D6 门禁扩展 PR；
3. P1 试点结论回填本 ADR（版本记录），再启动 P2 逐域迁移；
4. `docs/development/environment-variables.md` 附录在 P3 收口时标注各域的 Settings 落点。

## 关联实现/文档

- `docs/development/environment-variables.md`（210 名清单与裁决口径）
- `tools/dev/env_inventory.py`（清单/门禁；D6 的扩展对象）
- `backend/core/env_source.py`（env 来源与优先级唯一契约）
- `backend/core/job_timeout_config.py`、`backend/core/cors.py`、`backend/realtime/console_registry.py`（既有局部收敛先例）
- `docs/development/dependencies-and-quality.md`（依赖与 lock 流程）
- Agent Note：`docs/notes/architecture/2026-09-14-settings-convergence-adr-737.md`
