# 差异面不变量检查（invariant-diff，advisory 起步）

Status: implemented
Class: feature

## Decision

#855 收口的差集收缩落地：新增 `tools/dev/check_invariant_diff.py`，对 PR
新增行检查强制力覆盖图（governance 设计文档 §7.1）中 context-only 且可静态
判定的不变量子集。与 S11 的关系：S11 测「不变量文本在场」（源侧），本工具
测「产物未违反」（结果侧），同一不变量的两端——不重建行为验证层（§7.1 裁决）。

- 规则策展最小集（宁缺勿误报）：`pydantic-v1-api`（`.dict(` / `.parse_obj(` /
  `.from_orm(` / `class Config:`，backend/**/*.py）、`plural-table-name`
  （migrations 内 create_table/CREATE TABLE 引用 s 结尾表名，alembic 引号
  与裸 SQL 两种形态）、`bare-pytest`（agent scripts 内裸 pytest，
  `python -m pytest` 放行）；
- 只看新增行（diff `+` 行）：存量违规不误伤（backend 现存 `.dict(` 用例实证
  逐存量清洗不可行，diff 面棘轮只增不减）；
- **advisory 起步**：exit 0 只留痕（exit 1 仅 `--strict`，为转 BLOCK 预留
  接口，同 ai-drift 先例）；噪声数据收齐后按覆盖图棘轮裁决转 BLOCK；
- 接线：run_gates `invariant-diff` 入 check:pr 与 check:full；
  `GATE_TO_CI_ANCHOR` 登记 None + 理由（S5x 强制回答 CI 对应物问题——
  转 BLOCK 时与 immutability 同模式加 ci.yml step 并改映射）。

## Alternatives

- **直接 BLOCK**：否决——backend 现存 `.dict(` 用例证明规则在存量代码里
  已有违反先例，噪声面未测即阻塞会制造「为过门禁而洗白模式」的压力；
- **扫全量而非差异面**：否决——存量清洗是一次性大工程且不属于本 issue
  （差集收缩是棘轮式只增不减，不是大扫除）；
- **模式放进 ruff 自定义规则**：否决——ruff 插件维护成本高于独立脚本，
  且 plural-table-name/bare-pytest 本就不是 Python AST 层面的规则；
- **泛化「任何 s 结尾标识符」到非迁移文件**：否决——backend 业务代码里
  复数变量名合法，只约束 migrations 建表动作。

## Verification

- `venv/bin/python tools/dev/check_invariant_diff.py --self-test` 全绿
  （规则红绿双向 + 删除侧不报 + backend 外路径不扫 + 裸 SQL/引号两种
  建表形态；自测真逮过「裸 SQL 表名无引号漏判」缺口后修复）；
- `venv/bin/python -m ruff check tools/dev/check_invariant_diff.py
  scripts/run_gates.py` 通过；
- `check_governance_surface.py --check` 全绿（S5x 确认新 gate 登记配对）；
- 对本分支实际 diff 实跑：0 违规 exit 0；
- 判定真值核查：`.dict(`/`parse_obj(`/`from_orm(`/`class Config:` 为
  Pydantic v1 API；`create_table("host")` 单数放行、`"hosts"` 拦；
  `python -m pytest` 放行、裸 `pytest` 拦。

## Revisit

- advisory 数据（CI/check:full 留痕）收齐后裁决转 BLOCK：与 immutability
  同模式加 ci.yml step + `GATE_TO_CI_ANCHOR` 改映射 + 移除本条；
- 新不变量入覆盖图时同步加模式（棘轮）；模式误报经 allowlist 化处理并
  记录理由；
- `python -m pytest` 规则当前仅覆盖 agent scripts 的 .sh——若 diff 面出现
  docs/CI 内裸调用事故再扩路径。
