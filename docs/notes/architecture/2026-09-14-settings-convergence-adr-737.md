# 配置读取收敛 ADR 立项：分域 pydantic-settings 与裸读取边界（#737 deferred）

Status: implemented
Class: architecture

## Decision

承接 #737 收口的 deferred 项「向统一配置管理类收敛」，产出 **ADR-0042（Proposed）**：
`docs/adr/ADR-0042-settings-convergence-and-bare-read-boundary.md`。

本 Note 记录**为什么是提案而非直接实施**，以及提案的事实基线（供裁决时核对）：

- **事实基线（2026-09-14 实测）**：`backend/**`（不含测试与 `agent/scripts/`）325 处
  裸 `os.getenv`/`os.environ.get`；70 处为模块级 import-time 常量；默认值/类型转换
  分散且有三种私有 helper；`pydantic-settings` **未在依赖中**（需新增源 + lock + hash）。
- **本 ADR 解决什么、不解决什么**：#737 已解决「配置不可见」（210 名清单 + 二选一门禁）；
  本 ADR 只解决「没有单点类型/默认值/校验」。若认为残留代价可接受，方案 C（维持现状）
  是合法裁决——ADR 明确保留该基线。
- **本 ADR 的硬约束（D6）**：`env_inventory.py` 必须扩展解析 Settings 字段与
  `validation_alias`，否则「收敛」会变成新的不可见面——与 #737 的成果互锁。
- **载体可替换（方案 A）**：若不新增依赖，薄封装 accessor 可承载 D2/D3/D4/D6 全部判据；
  D1 的载体可替换，判据与门禁联动不变。

## 为什么走 ADR 而不是直接改代码

1. 全仓配置读取方式的改变属**方向级**（AGENTS.md：方向级决策使用 ADR）；
2. 涉及**新增依赖**（lock + 镜像 + 供应链面），需显式裁决；
3. 与 **agent 热更新**（hot-update 行级改 `.env` → 需 reload）和 **`env_source.py`
   来源契约**（进程 env > `.env.backend` > `backend/.env`）存在交叉，必须先定边界；
4. 迁移判据（D2）本身就是裁决对象：什么条件下迁移、什么条件下**保持裸读**，
   直接决定后续所有 PR 的取舍，不能在实现中隐含。

## Alternatives

- **直接开工试点（不立 ADR）**：否决——依赖与边界未裁决，试点会替全仓定下事实标准；
- **把 ADR 写成「全量收敛」**：否决——325 处一次性卷入，回退困难，且会把运维脚本与
  注入型协议键一并卷入（D5 已明确排除）；
- **不立 ADR、直接把残留代价记成 Revisit**：#737 已收口，此举等于默认不投入——
  若这是裁决结论，应在 ADR 的「方案 C」轨道上显式记录，而不是留在 Revisit 里无主。

## Verification

- `python tools/dev/check_governance_surface.py --check`：S1–S13、S5x 全绿
  （含 S12：ADR-0042 头部状态行 ↔ `adr/README.md` 主表行 ↔ DOC-MAP 行一致）；
- `python scripts/run_gates.py check:quick`：见 PR；
- 事实基线口径与 `tools/dev/env_inventory.py` 的清单（210 名）同源，可复算：
  `python tools/dev/env_inventory.py` 与仓库内 `grep -c` 计数。

## Revisit

- **裁决后**：接受 → 依赖 PR + P1 试点（1 控制面域 + 1 agent 域）+ D6 门禁扩展；
  接受方案 A → 用薄封装承载同样判据；不投入 → 在 ADR 状态上落 `Deprecated`/关闭并记录理由；
- **P1 试点结论**必须回填 ADR 版本记录（含 agent reload 实测结果），再启动 P2；
- 若期间 `pydantic` 主版本变动（v3），依赖与 `validation_alias` 写法需复评。
