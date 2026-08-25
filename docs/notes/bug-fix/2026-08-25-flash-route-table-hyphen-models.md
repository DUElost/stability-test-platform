# flash_firmware 指纹路由表缺连字符机型键

Status: implemented（flash_firmware v1.3.1）
Class: bug-fix

## Decision

`_MODEL_FAMILY_ROUTES` 补齐连字符型号键（MLD-LX2/MLD-LX3/ELA-LX2/ELA-LX3），
下划线键保留。v1.3.0 发布当天真机回归发现：`getprop ro.product.model`
实际返回 `MLD-LX3`（连字符），路由表只有下划线键 → 默认参数无法路由、
必须显式传 `family` 才能刷机。

两个易混来源（排查时的坑）：

| 来源 | 拼写 | 说明 |
|------|------|------|
| `adb devices -l` 的 `model:` 列 | MLD_LX3（下划线） | adb 侧派生字段 |
| `getprop ro.product.model` | MLD-LX3（连字符） | 路由表的实际输入 |

路由表键必须对齐 **getprop**，不是 adb devices 的显示列。

## Alternatives

- **manifest models 白名单兜底**：不解决问题——白名单在 family 确定之后才检查，
  路由表查不到 family 在更早处就 fail-fast。（NFS manifest 的 models 已于
  部署日改为双拼写，那是另一个独立缺口：单拼写会让白名单误拒。）
- **脚本端 normalize（大小写/符号归一）**：过度设计——机型集合小且封闭，
  显式枚举比猜测规则可审计；出现第三种拼写时加一行即可。

## Verification

- 单测 `test_flash_firmware_v131.py`：6 种拼写参数化路由 + fail-fast 信息
  含双拼写 + 连字符机型过 manifest 白名单 + main() 默认路由直达 skipped 冒烟。
- 生产回归（172.21.15.66）：v1.3.0 + 显式 `family:"MLD"` 时
  `decided_by=fingerprint, model=MLD-LX3` 整链通；v1.3.1 使 family 参数
  不再必需。

## Revisit

- 新机型接入时同时核对 getprop 实际值与 manifest models；两者拼写不一致
  是常态而非异常。
- 若后续 ELA 真机出现其它拼写变体，同法加键。
