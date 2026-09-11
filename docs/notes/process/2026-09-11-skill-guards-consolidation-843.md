# 存量 skill「踩坑守卫」显式化与非 Claude harness 可见性（#843）

Status: implemented
Class: process

## Decision

落实 `#843` 裁定的两项遗留（不新造规则，只归拢/显式化既有内容）：

1. **存量 4 个 skill 统一显式命名「踩坑守卫（负向约束）」**（判据 3 的稳定载体）：
   - `device-lease-release`：尾段守卫改为标题段（生产写授权 / 回查 / 表与文件禁令）；
   - `test-env-self-check`：新增守卫段（测试库红线 / `python -m` / 无 SQLite 退路 /
     WSL 5039 / 不猜键名）；**顺带修正漂移**——该 skill 仍宣传 `ALLOW_SQLITE_TESTS=1`
     退路，与 fixture 和 `testing.md`（`#1299` 已修）不一致，改为「无 SQLite 退路」；
   - `control-plane-deploy`：新增守卫段（部署源守卫 / 禁直连生产库迁移 / scan
     conflicts 先比对 / 热更新残留 / 不抢 `reload_config` / 版本门控顺序）；
   - `agent-host-onboard`:「禁止误配」表更名为标准标题，并把散落的负向约束（设备节点名
     以 `lsblk` 为准、`agent_host_id` 对齐、UNC 勿硬编码、双 ADB DEGRADED）归拢入段。
2. **非 Claude harness 的可见性说明**（`harness-adapters.md`）：`.claude/skills/` 仅
   Claude Code 自动加载；其他 harness 按适配表入口进入后直接读取对应 `SKILL.md`；
   清单以目录为准，不在文档复制（防漂移）。

## Alternatives

- **把守卫段统一挪到文末**（与新 skill 位置一致）：放弃——`agent-host-onboard` 的误配
  表紧邻 env 模板，移动会切断上下文；位置次于「稳定标题」这一要点；
- **在 adapter 文档列全部技能名**：放弃——清单会随 skill 增减漂移，指向目录更稳；
- **不动 `test-env-self-check` 的 sqlite 句**（仅做守卫段）：放弃——负向约束段刚要求
  「无 SQLite 退路」，同文件中保留相反表述自相矛盾，必须一并修正（并已属 `#1299`
  同源漂移）。

## Verification

- `python tools/dev/check_governance_surface.py --check` → 全绿（S7 校验 4 个 skill
  前端 matter 未受影响）；
- `grep -c "踩坑守卫" .claude/skills/*/SKILL.md` → 每个文件恰好 1 处（无重复段）；
- `grep -rn "ALLOW_SQLITE_TESTS" .claude/skills/` → 仅剩「不存在」的否定表述；
- `python scripts/run_gates.py check:quick` 通过。

## Revisit

- 下批 triage 类 3 个 skill 的触发条件见前一份裁定 Note（任一场景再次手工执行即建）；
- 若 harness 侧出现技能自动发现能力（如 CodeBuddy 支持仓库内技能目录），再议桥接方式，
  不提前复制多份。
