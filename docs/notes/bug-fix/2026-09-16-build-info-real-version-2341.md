# 控制面 build_info 报真实版本：清单/checkout 两种形态的真值 + 幽灵常量裁决（#2341 + #2572）

Status: implemented
Class: bug-fix

- 日期：2026-09-16（#2572 增补于 2026-09-17）
- 相关：`#2572`（两种部署形态下恒 unknown 的增补）、`#2286` / PR #2313（本单从其 Revisit 拆出）、
  `#2276`（「绿色安装旧版本」实证）、`#1930`（健康面禁止把未知当对齐的先例）、
  `#2155`/ADR-0040（Agent 侧 revision 显示面）

## Decision

### 1. 真值来源裁决：`release-manifest.json` > 显式 `unknown`

优先级 = **清单 > 显式 unknown**，**不**回落 `backend.__version__`。

真值本来就在同一棵树里：`tools/release/build_bundle.py` 产出的清单（`product.version` /
`source.revision`），安装链本身也以它 fail-closed（`tools/site_config/install.py` 比对
`manifest.product.version != expected_release` 即阻断）。运行时只**读**它，**不新增环境变量**
（新增会牵动 8 个 `.env*.example` 与 `tests/test_env_example_parity.py` 门禁，而清单已是既有
事实源）。

读不到清单（开发态 git checkout）→ **显式 `unknown`**：回落成 `"2.0.0"` 之类会再造一个
「看起来有答案」的假信息——那正是本单要消灭的形态。读清单是只读且**异常不致命**的
（启动期不允许因为一份清单缺失而起不来），失败记 warning。

> **本条已被 #2572 增补**（见 §5）：优先级改为 **清单 > checkout(git) > 显式 unknown**。
> 「不回落任何具体版本号」「异常不致命」两条不变。

### 2. 幽灵常量裁决：**删除** `backend/__init__.py:__version__`

三条出口里选「删除、只认清单」。依据：全仓 grep（`--include` 覆盖 py/sh/yml/json）
**零引用**——没有任何调用方读它；它自 `9763ba8a`（2026-05-05）引入起从未更新，而
`backend/main.py` 又把同一个值复制成第二处字面量（两处可各自漂移）。留着的唯一效果是让
下一个人以为「源码里有版本真值」。原地留一行注释说明真值去哪了，防止被重新加回。

Agent 侧的 `backend/agent/__init__.py:__version__` **不动**：它有真实消费者
（`backend/agent/api_client.py` 上报 `Host.agent_version`），是另一条链路。

### 3. `schema_target` label 裁决：**不加**

`database.schema_target` 是**安装期声明值**，`/health` 的 `alembic_head` 是**运行期实测值**，
语义不同；把它挂到 `build_info` 上等于造第三个事实源，且两者的对账关系（声明 vs 实测）应由
已有的健康面表达，而不是压进一个标签。故 `stability_build_info` 保持 `{version, commit}`。

### 4. 接线与「多进程不导出」约束

- `backend/main.py` 的 lifespan：`init_build_info(*resolve_build_info())`；
- 新增 `backend/core/release_manifest.py`（只读、异常不致命、回落显式），`_REPO_ROOT`
  用 `Path(__file__).resolve().parents[2]`——与 `core/schema_revision.py` 同款既有写法；
- `init_build_info` 的 docstring 写明 **`prometheus_client` 的 Info 指标在多进程模式下不导出**
  （官方约束），并加**运行期自检**：检测到 `PROMETHEUS_MULTIPROC_DIR` 即 warning 并指出正确
  改法（换带 label 的 `Gauge('stability_build_info', ..., ['version','commit'])`）。当前
  systemd 单元是单进程 uvicorn，故 `Info` 可用。这条自检读的环境名按门禁规则声明为**内部**
  （`tools/dev/env_inventory.py`），并重刷了生成清单文档（同时带出其他已合入 PR 的行号漂移）。

## #2572 增补：两种部署形态下的真值来源（2026-09-17）

**症状**：本单的修法在两种**现实**部署形态下都拿不到版本，`stability_build_info`
在生产恒 `unknown`——① 站点安装形态：s2 只在 bundle 内**校验**清单存在
（`stages.py:659`），落地循环只搬 4 个子目录，清单从未到过部署树；② 本机生产控制面是
**git checkout** 形态（unit 的 `WorkingDirectory` = 仓根，实测），那棵树里本就没有清单。

**裁决（取代 §1 的来源优先级；§2–§4 的结论不变）**：

1. **s2 落地清单**（`tools/site_config/stages.py`）：`shutil.copyfile(bundle/
   release-manifest.json, deploy_root/release-manifest.json)`，重跑直接覆盖——清单是
   发布物的一部分，不做「已存在则保留」的秘密式处理；
2. **reader 侧增第三档**：`resolve_build_info()` 在**清单不存在**时读 git——
   `version` = `checkout` / `checkout-dirty`、`commit` = `git rev-parse HEAD`（sha 形状
   校验同 `build_bundle._revision`）。**不**把 tag 拼成版本号：本仓 tag（`baseline-*`）
   不是产品版本，照拼一个「看起来像版本」的串同样是假信息。

**为什么在读取侧回落，而不是让部署脚本写一份清单**：写侧多一处会**静默过期**的副本
（`git pull` 后忘记重新生成 → 清单报旧 revision，比 `unknown` 更坏——又变回「有面板、
数据是假的」）。读取侧读到的是当次启动时那棵树的真实状态；站点形态没有 `.git`，探测
失败即回到 `unknown`，原语义不变。

**为什么 dirty 只算 tracked 改动**：判据与部署源守卫 `tools/dev/check-deploy-source.sh`
同款（那里把 tracked 脏工作树当硬条件）；未跟踪文件不是「与 HEAD 不同的已发布代码」。
本机生产控制面与开发工作树是同一棵树，`checkout-dirty` 正是最需要看得见的那一档。

**边界**：清单**存在但损坏**时不转去读 git——那只会掩盖一次真实的安装损坏。

## Alternatives

- **回落 `backend.__version__`**：那是第二处会漂移的字面量（且已删除），回落它等于把假信息
  的概率降低但不清零。否。
- **保留 `__version__` 并指定维护者**：需要一条「谁来更新」的流程，而它是源码态而非部署态，
  永远无法回答「这个进程现在跑的哪个 revision」。否。
- **新增环境变量承载版本**：见 §1，成本高（8 个示例 + parity 门禁）且清单已是事实源。否。
- **带 `schema_target` label**：见 §3。否（允许结论为「不加」，本单用了这个出口）。
- **只在注释里写多进程约束、不加自检**：注释不会在真出事的那天出现；自检只要一行且
  只在真设了该键时才响。选自检。

#2572 增补的备选：

- **让部署脚本/unit 生成清单**（`ExecStartPre` 写 `git describe` + commit，或让 runbook
  多一步）：见上「为什么在读取侧回落」——写侧会静默过期，而读取侧不会；且 unit 改动要
  在每台已装机上重装才生效（本次的新码随部署直接生效）。否。
- **从 tag 派生版本号**（`git describe --tags`）：本仓 tag 是 `baseline-*` 基线而非产品
  版本，`baseline-2026-09-03-2916-gb1bf88b0` 这种串会被当成版本号读走。否。
- **清单损坏时也回退读 git**：会掩盖真实的安装损坏（站点形态本来就没有 git，回退只会
  在「本该有清单的树」上发生）。否。
- **把 checkout 形态的 sha 也塞进 `version`**（如 `checkout-b1bf88b0`）：`commit` 已是
  全 sha，重复且让 version 变成两种语义的混合体；`checkout-dirty` 只承载「有未提交
  tracked 改动」这一个额外事实。否。

## Verification

- `python -m pytest tests/ -q` → **1191 passed**（含新增 `tests/test_build_info_version_source.py`
  7 条：结构守卫 ×2、默认路径解析、指标值落进导出序列、缺失/损坏/半缺清单的回落）。
- **红向反证**：把 `backend/main.py` 的调用改回 `init_build_info(version="2.0.0", commit="unknown")`
  → 结构守卫**红**并报出精确行号与理由；还原即绿。
- `python -m pytest tests/test_alert_metric_producers.py tests/test_grafana_dashboard_contract.py backend/tests/api/test_main_lifespan.py -q`
  → **14 passed**（`stability_build` 的生产者证据仍成立：`main.py` 仍在调用 `init_build_info`，
  其内部 `build_info.info(...)` 不变）。
- `python scripts/run_gates.py check:quick` → **10 gates 绿**（含 env-inventory：新增内部声明后
  文档生成块已重刷）；`ruff check` 改动文件全绿。

#2572 增补的验证：

- `python -m pytest tests/test_site_install.py tests/test_build_info_version_source.py -q`
  → **79 passed**（build_info 7 → 11：新增 checkout 真值、dirty 标记、无 git 容忍、
  损坏清单不回退；site_install 新增 1 条清单落地与幂等）。
- `python -m pytest tests/ -q` → **1442 passed**（根套件，7m30s）；
  `python scripts/run_gates.py check:quick` → **11 gates 绿**（含 ruff）。
- **反向验证**（4 条判据逐条去掉/放宽，对应用例必须红，实测均红且只有目标用例红）：
  删掉 s2 的 `shutil.copyfile` ⇒ `test_release_manifest_lands_at_deploy_root` 红；
  删掉 checkout 回落 ⇒ `test_checkout_without_manifest_reports_git_revision` 红；
  去掉 dirty 判据 ⇒ `test_checkout_dirty_marks_uncommitted_tracked_changes` 红；
  让损坏清单也回退 git ⇒ `test_broken_manifest_is_not_papered_over_by_checkout` 红。
- **真实代码路径实测**（临时检出，非 mock）：本 worktree（checkout 形态、无清单）→
  `('checkout-dirty', 'b1bf88b0613ac88e2ba2466ec9434e996ab0486f')`——与
  `git rev-parse HEAD` 逐字一致，`-dirty` 如实反映当时未提交的改动；非 git 目录
  （站点形态复刻）→ `('unknown', 'unknown')`。

## Revisit

- **部署态核对**（#2341 未做，需真站点）：`/metrics` 里 `stability_build_info` 的
  `version`/`commit` 应等于该站点 `release-manifest.json` 的 `product.version` /
  `source.revision`；只读核对姿势见
  [`production-diagnostics.md`](../../operations/production-diagnostics.md)，凭据不入仓。
  **#2572 更新**：checkout 形态（本机生产）已在代码路径上实测（见 Verification），
  部署后重启一次即可在 `/metrics` 核对；**站点形态仍需一次真实安装**——那之前
  `tests/test_site_install.py` 的落地断言是唯一证据。
- **站点安装的后置核对未加进 `verify`**：`verify.py` 目前不核对部署树内容，
  `release-manifest.json` 是否在部署根只有安装阶段（s2）与测试盯着。若首次真实安装后
  想再加一层，应在 `verify` 里加「部署根的清单版本 == `/metrics` 读数」的对账，
  而不是在安装器里重复。
- **导航页与指标的「同源收敛」未做**：导航页仍走安装期 `config.release.expected_release`
  （本单明确不改它，只让指标不再说谎）。两者口径是否合并另判。
- **多进程化时**：若将来上 `--workers` + `PROMETHEUS_MULTIPROC_DIR`，`Info` 会静默不导出——
  自检会 warning，但**改法**（换 Gauge）仍需人工实施；届时按 `init_build_info` 的 docstring 办。
- **服务账号与仓库属主不同时**（`git` 的 `dubious ownership` 拒绝）：探测会失败并
  回落 `unknown`——届时在 unit 里补 `safe.directory`，或改回部署期生成清单。当前
  本机 unit 的 `User=debian13` 与仓库属主一致，不触发。
- **`source.revision` 的展示**：当前只进指标；是否需要像 Agent 侧那样进 UI（#2155 的对称面）
  未裁决，另行判断。
