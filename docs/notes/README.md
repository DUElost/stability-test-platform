# Agent Notes（决策记录）

本目录记录「代码与文档装不下的那部分」：为什么这么决定、放弃了什么、
如何验证、何时应重议。与 ADR 的分工：**ADR 管方向级决策**（存储方案、
认证架构），**note 管其余非平凡决策**（工具取舍、契约细节、防御性
补丁的动机）。

## 何时写

非平凡变更 = 改变行为/契约/状态机/环境变量/部署/测试策略/文档结构，
或未来自己可能追问「为什么当年这么写」的改动。

- 每个非平凡 PR **至少附带/更新一条 note**（同 PR）；
- 该主题已有 note 时更新它，不重复建；
- 纯机械改动（typo、格式化、无行为变化的重构）豁免。

## 布局

```
docs/notes/
  feature/ bug-fix/ simplification/ architecture/ process/ testing/
  archived/          # 冻结区，只读
```

文件名：`yyyy-mm-dd-主题.md`。日期 = 主题首次提出的日期；考证不到时
用记录创建日（正文中注明原始事件日期）。

## 头部（结构固定，顺序不变）

```
# 主题

Status: proposed | implemented | rejected
Class: feature | bug-fix | simplification | architecture | process | testing
```

标题后必须保留一个空行（markdownlint MD022）。

## 正文四节

```
## Decision         决定了什么（写现状与事实，不写叙事）
## Alternatives     放弃的备选 + 代价
## Verification     验证手段（测试名 / 命令 / 门禁）
## Revisit          何时应重议（可选）
```

## 归档与废弃

- 已完成且不再需要指引后续工作的 note → 移入 `archived/`，修复所有入链，冻结；
- `proposed` 永续未实施 → 改 `Status: rejected` 并写明原因，或直接删除；
- `rejected` 只在「防止重犯同一个有吸引力的错误」时保留，否则删除。
