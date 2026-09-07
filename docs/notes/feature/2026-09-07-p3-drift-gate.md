# P3 drift gate（advisory）——freshness/declaration-drift/coverage-mismatch/overlap

Status: implemented
Class: feature

## Decision

ADR-0034 §2.7 P3 的真增量落地（不建 merge queue——主干 FIFO auto-merge 已存在）：

`ai_work.py drift` 子命令（只读 advisory gate）四类检查：

1. **freshness**：在窗记录 STALE（>24h 无心跳）→ 提示人工裁决（非死、不剔除——契约 §4 语义）；
2. **declaration-drift**：declared vs derived 双清单（声明未落地 / diff 未声明）——契约 §5.1 的 drift 提示结构化输出；
3. **coverage-mismatch**（MVP）：声明 `test_impact=none` 但 derived 触及测试相关路径（目录前缀 backend/tests/、backend/agent/tests/、tests/ + conftest.py/pytest.ini/vitest 配置 + *.test.*/*.spec.*）→ 提示；**direct 声明的 CI 记录核验未实现**——证据口径=夜间全量/合并后记录（契约 §6），Execution↔CI run 的关联机制需先有真实使用数据，诚实标 pending；
4. **overlap-hint**：在窗记录间 effective scope 的**顶层目录**交集（比 status 的组件级更低噪，对齐 ADR「overlap 粒度用顶层目录作 hint」）。

挂载与语义：

- **advisory 不阻塞**：有提示 exit 0 + `[advisory]` 行；`--strict` 有提示即 exit 1——**转 required 的接口预留**（当前不接任何阻塞路径；转 required 须独立裁决）；
- `run_gates` 新 `ai-drift` gate（`check:full`=夜间全量自动包含，**不进 quick/pr**——守合入路径 ~2min 注意力预算）；S5x 登记 `None` + 理由（CI runner 无本机 registry 数据）；
- registry 为空时 no-op（过渡条款提示）。

## Alternatives

- **direct 声明的 CI 记录核验一并做**——放弃：PR checks 口径会常态误报（契约 §6 明确），夜间全量记录与 Execution 的关联机制需真实使用数据先积累；MVP 只做本地可判定的 none-case。
- **advisory 也进 check:quick**——放弃：quick 是阻塞语义的快速轮，advisory 混入稀释信号；夜间全量留痕 + 本地按需已满足 P3 定位。
- **overlap 用组件级全路径**——放弃：status 已有组件级；gate 场景用顶层目录降噪（ADR 原文口径）。

## Verification

- `--self-test` 增 P3 纯函数红绿（is_test_path 正反、top_dirs 聚合、collect_drift_advisories 组合场景：STALE 提示/顶层 overlap 命中/derived 为空不误报 coverage）全过；
- 真实 registry 冒烟：declare 后 `drift` 输出 2 项 advisory 且**真实命中本 PR 的实际 diff**（声明未落地+diff 未声明清单正确）；advisory exit=0、`--strict` exit=1；清空后 no-op；
- 治理门禁 S1–S11+S5x 全绿（S5x 接受 `ai-drift: None` 登记及理由）；ruff 通过。

## Revisit

- **转 required**：需独立裁决（ADR §2.7 P3 备注既定）——候选触发=advisory 数据积累后评估误报率；
- **direct 声明核验**：待 Execution↔夜间全量 run 的关联机制（如 PR 号→merge commit→run id 链）有真实数据后增补；
- P4 Integration Planner 仍在「人已难判集成顺序」观察位。
