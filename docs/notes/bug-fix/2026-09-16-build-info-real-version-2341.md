# 控制面 build_info 报真实发布版本：读 release-manifest.json + 幽灵常量裁决（#2341）

Status: implemented
Class: bug-fix

- 日期：2026-09-16
- 相关：`#2286` / PR #2313（本单从其 Revisit 拆出）、`#2276`（「绿色安装旧版本」实证）、
  `#1930`（健康面禁止把未知当对齐的先例）、`#2155`/ADR-0040（Agent 侧 revision 显示面）

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

## Alternatives

- **回落 `backend.__version__`**：那是第二处会漂移的字面量（且已删除），回落它等于把假信息
  的概率降低但不清零。否。
- **保留 `__version__` 并指定维护者**：需要一条「谁来更新」的流程，而它是源码态而非部署态，
  永远无法回答「这个进程现在跑的哪个 revision」。否。
- **新增环境变量承载版本**：见 §1，成本高（8 个示例 + parity 门禁）且清单已是事实源。否。
- **带 `schema_target` label**：见 §3。否（允许结论为「不加」，本单用了这个出口）。
- **只在注释里写多进程约束、不加自检**：注释不会在真出事的那天出现；自检只要一行且
  只在真设了该键时才响。选自检。

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

## Revisit

- **部署态核对**（本单未做，需真站点）：`/metrics` 里 `stability_build_info` 的 `version`/`commit`
  应等于该站点 `release-manifest.json` 的 `product.version` / `source.revision`；只读核对姿势见
  [`production-diagnostics.md`](../../operations/production-diagnostics.md)，凭据不入仓。
- **导航页与指标的「同源收敛」未做**：导航页仍走安装期 `config.release.expected_release`
  （本单明确不改它，只让指标不再说谎）。两者口径是否合并另判。
- **多进程化时**：若将来上 `--workers` + `PROMETHEUS_MULTIPROC_DIR`，`Info` 会静默不导出——
  自检会 warning，但**改法**（换 Gauge）仍需人工实施；届时按 `init_build_info` 的 docstring 办。
- **`source.revision` 的展示**：当前只进指标；是否需要像 Agent 侧那样进 UI（#2155 的对称面）
  未裁决，另行判断。
