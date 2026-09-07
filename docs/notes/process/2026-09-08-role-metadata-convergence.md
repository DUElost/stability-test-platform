# Role 定位收敛——元数据+扩展点，Role Runtime 降级 deferred（契约 v1.5 / ADR-0034 v1.7）

Status: implemented
Class: process

## Decision

（用户 2026-09-08 裁决）不为了「完成 P2」补建 Role 运行机制；Role 定位收敛为：

- Role = **保留的 Execution 元数据与未来扩展点**（自由文本、无枚举；不参与路由、
  不加语义约束、非文件 ownership 边界——后者为既有条款，继续有效）；
- 当前**默认且唯一实际运行角色为 `implementation`**，registry 空串即视为该缺省；
- 特殊 Role 暂不进入主执行路径；**不要求** Harness 会话启动时自动注入 Role，
  **不要求**所有 Harness 对所有 Role 等价支持。

落地（4 处 living 文档 + 2 处 S12 派生索引）：

1. `execution-contract.md` §1.2 `role` 行重写（原「定义与供给细则归 P2 Adapter」
   条款撤回），状态行升 **Living v1.5**；
2. `ADR-0034`：§2.7 P2 行「会话启动时知晓自身 Role」从必交付**降级为 deferred
   capability**（P2 待交付仅剩 heartbeat wrapper），§2.1 补 Role Context 当前
   形态一句，状态升 **Accepted v1.7** + 版本记录；
3. `harness-adapters.md` §P2 Adapter：增「Role 非 Adapter 义务」段；
4. S12 派生面同步：`adr/README.md` 主表 + M7 看板、`DOC-MAP.md` ADR-0034 行与
   执行契约行。

## Alternatives

- **维持「P2 必须实现 Role Context 供给」原承诺**：否决——P1 落地后 registry
  在档记录的 `role` 仅出现过空串与 `docs` 两种值（后者为自由标注，非独立运行
  角色），Role 供给无真实消费方；为完成度补机制违背最小变更原则，P2 剩余真实
  价值集中在 heartbeat wrapper（§2.5 升格条件）。
- **顺带改 `ai_work.py` 把缺省 role 归一化写为 `"implementation"`**：本 PR 不做
  ——契约先行（§10 先后纪律）；「空串=缺省 implementation」的读法已使现行代码
  行为与契约一致，是否归一化存储见 Revisit。

## Verification

- 全库 grep 残留面核查：living 文档（contract / ADR / harness-adapters /
  DOC-MAP / adr README）无「Role 供给承诺」残留；命中均在 `docs/notes/`
  冻结历史记录（按惯例不改写，见 Revisit）；
- S12 索引一致性门禁通过：ADR 头部 v1.7 = 版本记录末项 = adr/README 主表版本
  前缀 = DOC-MAP 行末 version token = M7 看板（`Accepted` v1.7）；
- `python scripts/run_gates.py check:quick` 全绿（含 ai-work gate：declare
  scope 覆盖本 diff，无未声明文件）。

## Revisit

- `ai_work.py` 是否把缺省 role 归一化存储为 `"implementation"`（或 declare 输出
  提示）——等首个真实多 Role 场景出现再议，避免为空串读法提前编码；
- 特殊 Role（评审/规划等）进入主执行路径的触发条件**未定义**——出现真实需求时
  需新的用户裁决（届时走契约新版本 + ADR 增补）；
- `docs/notes/feature/2026-09-07-ai-work-p2-adapter-whoami.md` 中「whoami=
  『启动时知晓自身 Role』的最小实现」为历史表述，冻结不改写；如后续重启 Role
  语义设计，从新 note 起步并回溯修订本收敛条目。
