# memory-lint 增索引预算判定与无损压缩（#2156）

Status: implemented
Class: bug-fix

## Decision

`tools/dev/memory_lint.py` 增加两项能力，目标是把「索引热路径」从人工照看变成可判定、可执行：

1. **`--budget`**：量 `MEMORY.md` 的 UTF-8 字节数与条目数，按三级阈值判定——目标 18.0 KB、
   软触发 19.5 KB、硬墙 24.4 KB（2026-09-15 实测 harness 口径：体积超硬墙时只加载一部分，
   **静默丢条目**）。超软触发即 exit 1，可脚本化；阈值可用 `--target-kb/--soft-kb/--hard-kb`
   覆盖（测试与其他 harness 用），默认值以模块常量单点定义。
2. **`--fix` / `--fix --apply`**：对行宽超限（>150 字符）的索引行做**无损**压缩——把原行
   **逐字**追加进目标条目文件的「原索引条目」块（与 2026-09-15 手工迁入的 69 条同形，块首带
   HTML 注释说明可删），索引行替换为 ≤150 字符的指针（保留 `- [标题](file.md) — ` 前缀，优先
   在 `；`/`。` 分句边界断开，截断以 `…` 收尾）。**幂等**：原文已在目标文件里就不再追加。
   跳过条件如实报告：不是 `- [标题](file.md) — hook` 形态、目标文件不存在、前缀已占满行宽。

**契约收窄**：模块 docstring 原写「本工具绝不修改任何 memory 文件」，现改为「默认只读；唯一写
路径是 `--fix --apply`」。写入集中在 CLI 层的 `apply_index_fixes()`，`lint_memory_dir()` 仍不写
文件；`--apply` 不与 `--fix` 同用报 usage error（exit 2）。

**复检语义**：`--fix --apply` 真正改写后重跑 lint 与预算，退出码与报数反映**压缩后**的状态，
避免「修完了仍 exit 1」。

涉及文件：`tools/dev/memory_lint.py`、`tests/test_memory_lint.py`、本 note。

## Alternatives

1. **另建 `memory_compact.py`**：边界更干净（lint 保持纯只读），但 #2065 已把「查行宽超限」放进
   lint，压缩是它的直接后续动作；两个入口会让「查出来之后干什么」重新变成人的记忆负担。
   代价是只读契约要收窄——已在 docstring 与本 note 写明。
2. **`--fix` 默认落盘**：省一个 flag，但一个写 `$HOME` 数据的工具默认写盘，风险与收益不匹配；
   故默认只打印方案。
3. **`--fix` 做语义摘要**（压成一句话而非截断）：摘要要判断「哪句重要」，属语义决策且会丢信息，
   与「原文一律先迁入条目文件」的无损前提冲突。故只做机械截断。
4. **把预算检查接进 CI**：沿用 #1585 裁决（检查对象在 `$HOME`，跨机器不成立），不进 CI；改用
   本机例行 + ZCode hook（store 侧探针已装，语义待实跑确认）。

## Verification

- `pytest tests/test_memory_lint.py -q` → **42 passed**（原 26 → 42）。新增覆盖：预算三级判定与
  退出码、压缩无损性（原行逐字出现在目标文件）、幂等性（二次运行报「没有行宽 >」且文件不变）、
  陈旧方案不重复追加附录块、三类跳过条件、干跑不写盘、`--apply` 缺 `--fix` 报 usage error。
- 真机干跑（**只读**，主工作树 store）：`--repo-root <主仓库> --harness zcode --budget` →
  `93 条 / 17.3 KB — 预算内（目标 18.0 KB）`；同 store `--fix` → `没有行宽 > 150 的行`
  （该 store 的行宽已于 2026-09-15 清理）。
- `check:quick`：见 PR 描述。

## Revisit

- 硬墙 24.4 KB 是 2026-09-15 的**单源**实测（会话启动提示原文），若 harness 侧数字变化，改
  `INDEX_BUDGET_HARD_KB` 常量即可；
- 若 ZCode hook 探针证实 stdout 会进模型上下文，把 `--budget` 接进 `SessionStart` hook 成为真门禁；
- 若出现「同一 store 多会话并发压缩」，`apply_index_fixes()` 的读-改-写需要加锁，或改为按行定位
  的原地替换。
