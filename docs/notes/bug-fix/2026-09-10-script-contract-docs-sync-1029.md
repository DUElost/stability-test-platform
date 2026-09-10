# 脚本契约文档与实现同步（#1029）

Status: implemented
Class: bug-fix

## Decision

#1029（R08-F11，P3）：脚本契约文档与实现三处偏差，按**当前实现**同步，不顺带
扩展能力：

1. `script-versioning.md` 目录示例宣称 `<entry>.{py,sh,bat,cmd}`，而扫描器
   `_SUPPORTED_SUFFIXES` 只识别 `.py`（python）/`.sh`（shell）——示例改为
   `.{py,sh}`，并显式写明 `.bat/.cmd` 不受支持（历史文档口径收口），
   引用 `script_catalog._SUPPORTED_SUFFIXES` 作为事实源；
2. 参数表单示例 `"type": "int"` → `"integer"`（后端 `_VALID_PARAM_TYPES` 只收
   `integer`，`ScriptVersionDialog` 占位示例此前在教用户写必败 schema）；
3. capabilities 偏差（后端返回、前端 `ScriptEntry` 未声明）**收口跟 #787**，
   本单不动类型——避免与 #787 的实现撞车。

`docs/operations/linux-first-initial-migration-checklist.md` 已正确表述
「`.bat/.cmd` 会被扫描忽略或 API 拒绝」，无需改动；`docs/archive/` 下的历史
冲刺文档不改（历史记录）。capabilities.json 的运维文档
（new-specialty-onboarding-runbook.md）与实现一致，未动。

与 ADR-0033 的关系：无直接约束。唯一注意点已落实——文档把 `capabilities.json`
表述为**现行扫描契约**，不写成长期权威清单（D3 未来以 package manifest ×
DB catalog 为唯一权威），避免钉死过渡形态。

## Alternatives

- 顺带给扫描器加 `.bat/.cmd` 支持：issue 明确禁止（Linux-first Agent 无此需求，
  且会连带 Windows 批处理的测试与安全面）；
- 本单补 `ScriptEntry.capabilities` 类型：与 #787 的 capabilities 收口撞车，
  放弃。

## Verification

- `grep` 全 docs 复核：`.bat/.cmd` 残留仅剩本单新表述（「不受支持」）、
  linux-first checklist（口径一致）与 archive（历史不动）；
- 前端 `tsc --noEmit` 0 错误、`eslint ScriptVersionDialog.tsx` 干净；
- 无 Python 改动（test_impact=none）；后端 `_VALID_PARAM_TYPES` /
  `_SUPPORTED_SUFFIXES` 实读核对（`integer` ✓、`.py/.sh` ✓）。

## Revisit

- `ScriptEntry.capabilities` 类型声明与前端消费 → #787；
- 若未来 Windows 节点（bat 工具）进入支持范围，需先扩
  `_SUPPORTED_SUFFIXES` 并回改本文档——顺序不能反。
