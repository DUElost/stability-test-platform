# Plan 级版本选择机制缺口的现场记录（换版本为何要新 Plan）

Status: implemented（方案 A 已由 flash_firmware v1.3.6 per-model versions 落地，2026-08-29，PR #501）
Class: architecture

## 现状

平台 Plan 步骤**无参数通道**（ADR-0029 D1 `plan_step.params_override`
挂起），flash_firmware 的目标版本由「指纹路由 + `{family}/latest.json`
族级指针」决定。族 = 机型集合（MLD 族含 LX2/LX3），指针只能指向**一个**
版本 → 多机型多固件并存时：

- 换版本 = 改 latest.json（**影响同族全部机型**，靠 manifest models
  白名单 fail-fast 兜底，窗口期需人工错开派发）
- 2026-08-28 LX2 V62 批量验证即采用「临时切指针 + 刷完切回」补丁形态

## 方案 A（推荐）：族级指针升级为 per-model versions 映射

```json
{"versions": {
  "MLD_LX2": "V552AA-HONOR-LX2-16-260810V62",
  "MLD_LX3": "V552AA-HONOR-LX3-16-260804V71"
}}
```

- 路由：family → 机型 → version → 固件目录；单键 `{"version": ...}` 兼容回落
- LX2/LX3 并行刷写，无需切指针/新 Plan
- 改动面：flash_firmware 新版本（`_resolve_by_fingerprint` 机型级版本解析）
  + 单测 + 族指针文件兼容

## 否决项

- **恢复 D1（params_override）**：控制面迁移 + dispatcher + precheck +
  前端全套成本，此前评估收益仅「计划级显式选版本」——per-model 映射覆盖
  同场景且改动面小得多。
- **每机型拆族（如 MLD2）**：路由表与固件布局污染，历史迁移成本高。

## 触发

- 需要同时维护 LX2/LX3 两条固件线（当前已出现）时实施方案 A。
