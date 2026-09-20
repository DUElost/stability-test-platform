# skill `type` 分型进 S7 校验面：枚举 + event 登记表 + 复查期（#2864）

Status: implemented
Class: bug-fix

## Decision

**缺口**：`type` 决定 HOLLOW 判洞窗口（`persistent` 14 天 / `event` 60 天，
`tools/dev/skill_usage_report.py::HOLLOW_DAYS`），却不在任何校验面内——给它加一行
`type: event` 即把窗口放大 4.3×，无 allowlist、无登记、无审计痕迹。门禁的判据源
握在被检方自己的文件里。

**落点**：`tools/dev/check_governance_surface.py` 新增 **S7b**（纯函数
`check_skill_type_registry` + `_skill_frontmatter_fields` 解析器）：

1. `_SKILL_TYPES = {persistent, event}`：**未知取值 → 红**（拼写错误不再被静默降级
   掩盖；未来新增分型必须显式过门禁）；
2. `_SKILL_EVENT_TYPE_REGISTRY`：`event` 型必须登记**批准依据 + 复查期**（`YYYY-MM-DD`），
   **未登记 → 红**（"自授窗口"的正面堵口）；
3. **复查期已过 → 红**（逼一次重新裁决；报错带依据前缀便于定位）；
4. **登记条目与实际不符 → 红**（改回 `persistent` 或 skill 已删 ⇒ 台账只减不增）。

存量两条按 **owner 裁决 2026-09-19**（`docs/design/2026-08-governance-surface-protection.md`
§8 待决点行 + §9 修订记录 2026-09-19 条：全 harness 零作业触发证据成立，但低频是场景属性）
登记，复查期限 +90 天 = `2026-12-20`。

**接线**：`run_check()` 扫描 `.claude/skills/*` 时收集分型并调用 S7b（`today` 取当天）。
该门禁在 CI 的 lint job 有 `--self-test` + `--check` 两条
（`.github/workflows/ci.yml:291-292`）——所以本守卫**有 CI 对应物**（此前 known gap 是
`gov-skills` 用量面为「本机转录依赖」而本机-only，本单不涉）。另新增
`tests/test_skill_type_registry.py` 把「单一事实源」与三条反例钉进 PR 路径。

## Alternatives

- **只加枚举、不加登记表**：否决——枚举挡不住 `persistent → event` 的自我放宽，
  而缺口的本质是「豁免授予无留痕」。
- **登记表放 markdown 表格**：否决——解析脆弱、且不随代码评审留痕；放门禁代码里与
  `_SHARED_ALLOWLIST`（`tests/test_agent_import_boundary.py`）同族，diff 即审计。
- **未知取值沿用「降级 persistent」不加红**：否决——降级是判洞侧的安全兜底，但
  「写错分型」本身需要可见，否则未来新增分型永远无人把关。
- **把 `event` 窗口改成环境变量/配置**：否决——窗口是治理判据，进程级可改等于把
  判据源搬出仓库。

## Verification

- `tools/dev/check_governance_surface.py --self-test` → **OK**（新增 8 条 S7b 红/绿样例；
  夹具按登记表**动态生成全集基线**，登记表增删时夹具不漂移）；
- `--check` → **OK**（存量两条已登记且在期）；
- **反向验证（门禁有牙）**：
  ① 新建未登记 `type: event` 探针 skill → `[BLOCK] S7b …未登记——60 天判洞窗口是豁免面…`；
  删除探针后复绿；
  ② `sed` 把复查期改为 `2026-01-01` → 两条 `[BLOCK] S7b …复查期已过（2026-01-01，今天 2026-09-20）`
  （附依据前缀）；还原后复绿；
- `pytest tests/test_skill_type_registry.py -q` → **4 passed**；
- `ruff check`（门禁 + 新测试）→ All checks passed；
- `check:quick` → **[OK] 12 gates**；`check:pr` → **[OK] 21 gates**
  （含 gov-surface 的 `--self-test` + `--check` 两条、agent-tests、pr-migrate）。

## Revisit

- **复查期到期（2026-12-20）时门禁会红**：这是设计意图——届时重新裁决两条 `event`
  skill 的保留（或续期并更新复查期）。不是故障，不要绕过。
- **新增第三种分型**：必须同时改 `_SKILL_TYPES` 与 `HOLLOW_DAYS`；
  `tests/test_skill_type_registry.py::test_allowed_types_match_hollow_windows` 会拦单侧改动。
- **判据源完整性可外推**：任何「被检方文件决定自身豁免」的形态（同类 frontmatter 开关、
  自报类型字段）都应进 S7b 同族校验；本单只收口 skill `type` 这一处。
