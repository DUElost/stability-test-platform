# ADR-0054：Agent 与控制面的共享契约包——`backend/agent/contracts/`

- 状态：**Proposed** v0.1（2026-09-25 起草；待 owner 裁决）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-09-25
- 决策者：owner（待裁决）；起草：平台研发组
- 归属域：semantic-ownership control-plane-split
- 标签：agent, import-boundary, shared-contract, import-linter, ADR-0040, ADR-0051
- 关联：[#3298](https://github.com/DUElost/stability-test-platform/issues/3298)（实施单，方案已在单内选定）
  / [#738](https://github.com/DUElost/stability-test-platform/issues/738)（pipeline_validator 双端拷贝）
  / [#739](https://github.com/DUElost/stability-test-platform/issues/739)（Agent→控制面方向的 import 边界，`tests/test_agent_import_boundary.py`）
  / [ADR-0001](./ADR-0001-control-plane-and-agent-architecture.md)（控制面 / 执行面分层）
  / [ADR-0040](./ADR-0040-deployment-artifact-digest-protocol.md)（`agent-code` 部署单元与 digest 算法）
  / [ADR-0051](./ADR-0051-release-unit-and-content-addressing.md)（发布单元模型）
  / `.importlinter` C3（#3291 引入的「控制面不直接 import `backend.agent`」合约）
- 版本记录：v0.1（2026-09-25）首次提出，D1–D6 待裁决

## 1. 背景

### 1.1 事实面（读 `main@a3ad5fc` 得到，无推测）

| 事实 | 出处 |
|---|---|
| Agent 在主机上以**顶层包 `agent`** 运行，部署目录只有 `agent/` 与运行时工件 `schemas/` 等，**没有 `backend` 包** | `backend/agent/install_agent.sh:637-640`（`PYTHONPATH=/opt/stability-test-agent`、`python -m agent.main`）；`backend/agent/DEPLOY.md` 目录布局 |
| 因此 Agent 代码里的 `from backend.core.X import …` 在主机上**必然** `ImportError`，只能靠兜底走 agent 自带副本 | `backend/agent/job_runner.py:156-159`（`pipeline_validator`）；`backend/agent/legacy_aee.py:9-12`（`LEGACY_AEE_SCRIPT_NAMES`） |
| 已存在的「同一定义两份实现」 | `backend/core/pipeline_validator.py` 与 `backend/agent/pipeline_validator.py` **字节级相同**（138 行，#738）；`backend/core/legacy_aee.py` / `backend/agent/legacy_aee.py`；`backend/services/artifact_digest.py` / `backend/agent/artifact_digest.py`（ADR-0040 D1 的双侧镜像，靠字节级等价测试维持） |
| 已存在的「控制面伸手进 agent」：C3 基线 5 条 | `.importlinter` C3：`core.aee_metadata → agent.aee.metadata`（`core/aee_metadata.py` 本身是再导出壳）、`services.agent_log_signals → agent.watcher.contracts`、`services.dedup_extract → agent.aee.event_dirs`、`services.host_health_probe → agent.kernel_usb_faults`、`scripts.migrate_watcher_aee_state_keys → agent.aee.state_migration` |
| 上述被伸手的模块**本体**只依赖标准库，但经由**包 init 链**把 agent 运行时拉进控制面进程 | `backend/agent/watcher/__init__.py` 导入 manager / emitter / puller / device_watcher 等；`backend/agent/aee/__init__.py` 导入 processor 等 |
| Agent→控制面方向已有静态守卫，共享层靠「模块体纯度」白名单放行 3 个 `backend.core` 模块 | `tests/test_agent_import_boundary.py` 的 `_SHARED_ALLOWLIST`（`legacy_aee` / `pipeline_validator` / `metrics`） |
| `agent-code` 部署单元 = agent 源码树（沿用排除集）+ `pipeline_schema.json`；三处排除集同源锁定 | ADR-0040 D1；`backend/services/host_updater.py` `_TAR_EXCLUDES`、`stp_agent_priv.py::FIXED_EXCLUDES`、Ansible `agent_deploy`；`tests/test_ansible_digest_contract.py` |
| `pipeline_validator` 用 `__file__` 的相对深度定位 schema | `backend/agent/pipeline_validator.py:13`：`Path(__file__).resolve().parent.parent / "schemas" / "pipeline_schema.json"` |
| Ansible 控制机侧以 stdlib-only 方式加载 **agent 侧** digest 实现 | `tools/ansible/compute_deploy_digest.py`（`tests/test_ansible_digest_contract.py` 第 1 面） |

### 1.2 问题定性

共享定义现在靠三种方式维持：**复制 + parity 测试**、**`except ImportError` 兜底**、**控制面直接 import agent 内部模块**。三者的根因相同：仓库内的包名（`backend.agent`）和主机上的包名（`agent`）不一致，**agent 包之外的任何位置在主机上都不存在**。只要共享代码放在 agent 包外，就一定需要兜底或副本。

所以共享代码只有两个可能的物理位置：**agent 包内**（天然随 `agent-code` 下发），或**另起一个发布单元**。

## 2. 决策

### D1 归属：`backend/agent/contracts/` 是双方共享定义的唯一归属

- Agent 与控制面**都必须一致**的定义（数据形状、路径与命名约定、校验规则、摘要算法、签名词表），唯一实现放在 `backend/agent/contracts/`。
- 随 `agent-code` 下发：它本来就在 agent 源码树内，ADR-0040 D1 的输入集定义、三处排除集、digest 合约测试**都不改**；不新增 ADR-0051 意义上的发布单元。

### D2 纳入判据：只收「契约」，不收「逻辑」

纳入 `contracts/` 的模块必须同时满足：

1. 模块体只依赖标准库，外加**逐条登记**的可选第三方依赖（当前仅 `jsonschema`，缺失时的降级行为与现状一致）；
2. import 时无 I/O、无全局副作用；
3. 不 import agent 的其他模块，也不 import 任何控制面包。

Agent 的运行逻辑（采集、执行、状态迁移）**不属于契约**，即使控制面也要用。例如 `kernel_usb_faults` 的采集线程留在 agent，只有它的解析函数与签名词表可以作为契约抽出。

### D3 导入规则

- **Agent 侧**：同包**相对导入**（`from .contracts.pipeline_validator import …`、`from ..contracts import …`），不写 `backend.agent.contracts` 绝对形式，也不再写任何 `backend.core` 回退。相对导入在 `backend.agent` 与 `agent` 两种布局下都成立。
- **控制面侧**：绝对导入 `backend.agent.contracts.<module>`。
- `contracts/__init__.py` **保持为空**，不做 re-export，避免重演 `watcher/__init__.py` 把运行时拉进来的问题。

### D4 门禁

- **C3**（`.importlinter`）增加一条通配忽略：`backend.** -> backend.agent.contracts.**`。控制面 import `contracts` 放行，import agent 其他模块仍然失败。已在 import-linter 2.15 上实测：放行 `contracts`、拦截其他子模块，两个方向都成立。
  这条忽略行必须和**第一次搬迁同一个 PR** 加入：`unmatched_ignore_imports_alerting = error` 下，零匹配的忽略行本身会报错。
- **C6 纯度**用 AST 测试守，不用 import-linter。实测 `forbidden` 合约在 `forbidden_modules` 写祖先包（`backend.agent`）时**静默不生效**（`contracts` 导入 agent 运行时模块仍判 KEPT）；逐个列举兄弟模块又会随 agent 演进漂移。做法：扩展 `tests/test_agent_import_boundary.py` 的同一套 AST 扫描，对 `contracts/` 加一条更严格的判据——只允许标准库、同包相对导入和 D2 登记的第三方。
- `_SHARED_ALLOWLIST` 里的条目随搬迁逐条删除；搬完后该白名单只剩 `backend.core.metrics`（best-effort 指标，不属于契约，见 §3 非目标）。

### D5 每次搬迁的完成定义（缺一不算完成）

1. 旧副本删除（`backend/core/…` 或 agent 原位置），**不留再导出壳**：壳会让测试 patch 目标分叉，产生假绿（与 #3292 不留重导出同理）。调用方与测试的 patch 目标在同一 PR 改指；
2. 对应的 `except ImportError` 兜底分支删除；
3. parity / 字节级等价测试改为单实现测试，或删除；
4. `.importlinter` C3 基线的对应行删除（`unmatched_ignore_imports_alerting = error` 会强制执行）；
5. `_SHARED_ALLOWLIST` 的对应条目删除（`test_shared_allowlist_entries_are_still_used` 会强制执行）。

### D6 运行时工件定位

契约模块**不得**靠 `__file__` 的相对深度定位运行时工件。`pipeline_validator` 现在用 `parent.parent / "schemas"`，搬进 `contracts/` 后多一层目录会解析错。做法：由单一定位函数显式处理「仓库布局 / 主机布局」两种情况，或由调用方传入路径；首批搬迁时一并落地，并补两种布局各一条测试。

## 3. 范围与非目标

- **非目标**：agent 自身 `backend.agent.*` / `agent.*` 双形态导入的兜底（39 处 `except ImportError` 中的多数）。那是 agent 内部的包名问题，不是共享契约问题，本 ADR 不宣称会消除它们。
- **非目标**：`backend.core.metrics`。它是 best-effort 指标原语，agent 侧缺失时 no-op，不是双方必须一致的定义。
- **非目标**：改变 agent 的发布或安装方式（见 §4 备选 B、C）。

## 4. 备选方案与权衡

| 方案 | 做法 | 结论 |
|---|---|---|
| **A（采纳）** | `backend/agent/contracts/`，随 `agent-code` 下发 | 零部署改动；相对导入在两种布局下都成立；兜底与副本可以真正删掉 |
| B | 仓库顶层 `backend/contracts/`，随 agent 一起下发为主机上的 `contracts/` | **否决**：仓库里叫 `backend.contracts`、主机上叫 `contracts`，包名错位原样复现，又要写兜底；还要改 `agent-code` 输入集、三处排除集、digest 合约测试与 ADR-0040 D1 |
| C | 独立发布单元（pip 包装进 venv，按 ADR-0051 内容寻址） | **暂不采纳**：48 台主机的 venv 安装流程要改；控制面与 agent 之间引入版本偏斜这个新维度。收益（独立演进版本）目前没有需求。复议条件见 §7 |
| D | 维持现状，只补 parity 测试 | **否决**：这正是 #738 记录的问题形态；C3 基线无法下降 |

## 5. 落地顺序（每步一个 PR，独立可回滚）

| 步 | 内容 | C3 / 白名单变化 | 注意 |
|---|---|---|---|
| 1 | `pipeline_validator` + `legacy_aee` 常量表；D4 的 C3 通配忽略行与 C6 AST 测试同 PR 引入 | `_SHARED_ALLOWLIST` −2 | D6 schema 定位；控制面调用方 `plan_dispatcher_sync`、`api/routes/plans` 及其测试改指；`test_pipeline_validator_parity_738.py` 改为单实现测试 |
| 2 | `aee/metadata`、`aee/event_dirs`、`watcher/contracts` | C3 −3；`core/aee_metadata.py` 再导出壳删除 | 均为 stdlib-only；agent 内部调用方同 PR 改为相对导入 |
| 3 | `artifact_digest` 的**规范化算法**部分 | 字节级等价测试改为单实现 | 控制面侧输入集枚举（`host_updater._iter_payload_files`）留在 services；`tools/ansible/compute_deploy_digest.py` 的加载路径同 PR 更新，`test_ansible_digest_contract.py` 保持绿 |
| 4 | `kernel_usb_faults`（解析函数 + 签名词表）、`aee/state_migration` | C3 最多 −2 | 逐个判断：能抽出纯函数的抽，抽不出的留在 C3 基线里，并在 `.importlinter` 注释写明终态出口 |

预期终态：C3 基线 0–2 条；`pipeline_validator`、`legacy_aee`、`artifact_digest` 三组双份实现归一。

## 6. Verification（本稿事实核对方式）

- §1.1 各行均读 `main@a3ad5fc` 的源码与文档核对，出处已逐条标注；
- D4 的两条 import-linter 行为（通配忽略、祖先 forbidden 静默不生效）在临时夹具上以 import-linter 2.15 实测；
- 本稿不改代码。落地各步的验证以 D5 的五项完成定义为准，加上 `check:quick`、`tests/test_agent_import_boundary.py`、`tests/test_ansible_digest_contract.py` 和 agent 测试集。

## 7. Revisit（复议触发条件）

- Agent 与控制面出现**需要独立演进版本**的共享定义（例如需要同时兼容多代 agent 协议）：重议备选 C；
- 某个契约需要登记新的第三方依赖：逐条评估，不做一次性放宽；
- `contracts/` 规模明显超出「定义」范畴（开始出现运行逻辑）：说明 D2 判据被突破，需要回审。

## 8. 关联实现 / 文档（落地时同步）

- `.importlinter`（C3 通配忽略行；逐步删除基线行）
- `tests/test_agent_import_boundary.py`（C6 判据；`_SHARED_ALLOWLIST` 收缩）
- `backend/agent/DEPLOY.md` 目录布局（增加 `contracts/`）
- `docs/design/2026-semantic-ownership.md`（Accepted 后视需要补 owner 行）
