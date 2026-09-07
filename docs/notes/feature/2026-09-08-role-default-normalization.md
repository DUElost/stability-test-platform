# role 缺省归一化 + Role 扩展再开启条件（契约 v1.6 / ADR-0034 v1.8）

Status: implemented
Class: feature

## Decision

上一单（`2026-09-08-role-metadata-convergence.md`，契约 v1.5 / ADR-0034 v1.7）
Agent Note Revisit 留档两项，本单按用户指示闭环：

1. **role 缺省归一化**：`ai_work.py declare` 缺省（未传 `--role`）与显式
   `--role ""` 一律**写入** `implementation`（新纯函数 `default_role`，
   `DEFAULT_ROLE` 常量）；declare OK 输出行补 `role=<值>`；`--role` help 注明
   缺省语义。历史记录空串同义读取、**不迁移**（契约 §1.2 v1.6：空串=缺省
   implementation 的既定读法保持不变，写侧归一化只影响新记录）。
2. **Role 扩展再开启条件成文**（契约 §1.2 v1.6 新段）：特殊 Role 进入主执行
   路径仅当①出现**声明面消费 Role** 的真实需求（某机制需按 Role 区分行为：
   差异化登记纪律、门禁判定、overlap 处理等）且②经用户裁决以契约新版本 +
   ADR 增补落地；「多一种标签写法」「想更细的身份标注」不构成触发；触发前
   registry 接受自由标签值但一律无语义。

契约先行：§1.2 role 行与再开启条件段先落（Living v1.6），实现同 PR 跟进
（§10 先后纪律，#978/#946 先例：契约与实现同 PR 收口）。

## Alternatives

- **维持 Revisit 原判（等首个真实多 Role 场景再议）**：被用户否决——本单即
  用户指示「对留档的问题进行解决」的直接产物；且 v1.7 已定「默认
  implementation」，归一化只是把既定缺省落到存储层，不再属于提前编码。
- **迁移历史空串记录为 `implementation`**：否决——Registry 是声明面，
  批量改写历史记录无信息增益且违反「空串同义读取」的最小口径；`status`
  输出保持原样字段值。
- **`status`/`whoami` 对空串做展示层映射**：否决——展示层派生会掩盖记录
  原貌，与「声明面所见即所写」一致；历史空串随时间自然稀疏。
- **把再开启条件写进 ADR 正文而非契约**：否决——细则归契约（ADR v1.1 起
  收缩为决策要点），ADR v1.8 版本记录留裁决指针即可。

## Verification

- `ai_work.py --self-test` 通过（新增 role 归一化红绿断言：None/空串→
  implementation、显式值透传）；
- `python scripts/run_gates.py check:quick` 7 gates 全绿（含 gov-surface
  S12：ADR 头部 v1.8 = 版本记录末项 = adr/README 主表 = DOC-MAP 行末 token
  = M7 看板；ai-work gate：declare scope 覆盖 diff）；
- 与在窗 Execution `docs-857-root-claude-symlink` 的目录级 scope 包络经
  实际 diff 交叉验证零文件交集（其实际改动=AGENTS.md/CLAUDE.md/
  harness-adapters.md/check_governance_surface.py/其 process note）。

## Revisit

- 无新增遗留。v1.5 收敛 note 三项 Revisit 终态：①②本单闭环，③（whoami
  feature note 历史表述）维持冻结不改写的永久决定；若再开启条件未来命中，
  从新 note 起步并回溯修订本条目与契约 §1.2。
