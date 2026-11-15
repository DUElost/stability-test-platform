# Monkey AEE 去重汇总与 Jira 交接流程固化为 skill

Status: implemented
Class: process

## Decision

新增 `.claude/skills/monkey-aee-dedup-handoff/SKILL.md` 与同目录 `scripts/`（10 个参考脚本），
把 2026-11-05（run 1179/1193/1195/1198、1190/1196、1200）与 2026-11-12（P535AA 四 run、
8 个 build）两批实跑的「Monkey 专项 AEE 日志跨 PlanRun 去重汇总 → 交付共享根导出代表证据
 → 产出或回填 Jira 表单」固化为可检索的 SOP。影响面：新增 skill 目录与一条 process note；
不改生产代码、不改契约、不改环境变量、不改 CI。

SKILL.md 按四段结构（前置检查 → SOP → 后置验证 → 踩坑守卫）写 8 个阶段（A–H）、
26 处「预期：」与 18 条负向约束；术语按 `docs/design/2026-log-chain-global-semantics.md` §4
加限定语，规则句按 ADR-0059 只用「必须 / 不得 / 建议 / 可以」并写出执行者。
`scripts/` 收录两套实跑管线（`per-build` / `batch`）的清洗版：去掉硬编码 DB 口令与内网 IP
全地址、标注 `CONFIG` 区、查库改环境变量并 fail-closed；两套管线的数据契约不同，SOP 中
明确不得混用。

关联：`docs/notes/process/2026-10-30-monkey-aee-jira-handoff-reference.md`（经验参考与建设
输入 note）与 `docs/operations/2026-10-30-monkey-aee-jira-handoff-reference.md`（权威阶段与
计数口径）。本次不改动这两份文档；skill 在开头与附录引用它们，不重复定义协议。

## Alternatives

- 只写 SKILL.md 而不固化脚本：`/home/debian13/tmp/<campaign>_*/` 下的实跑脚本含硬编码凭据、
  常量写死且属 `/tmp` 临时产物，机器重建即失；下一次仍需从零重写。放弃。
- 把参考脚本参数化为通用工具或接入 CI 门禁：脚本未在现场参数下复跑、无测试，参数化会引入
  未验证代码；SKILL.md 已明确「不得当作生产工具或 CI 门禁」，超出本轮范围。
- 写进 ADR：本轮无方向级裁决，ADR 过重；按 `docs/notes/README.md` 的分工落在 note。
- 并入 `AGENTS.md` 常驻：该流程不是每次会话都要用，按 skill 候选四判据应独立成 skill，
  不占常驻契约的注意力预算。

## Verification

- 脱敏：全 skill 树 `grep` 无硬编码口令片段、无内网 IP 全地址、无硬编码 `PGPASSWORD`；
  UNC 用 `172.21.x.x` 脱敏形态；脚本内的 `*.DEC` 与事件目录名不含设备串号。
- 语法：10 个脚本 `python -m compileall` / `py_compile` 通过；提交前删除 `__pycache__`。
- 门禁：`scripts/run_gates.py check:quick`；`ip-leak` gate 对新增 `.md` / `.py` 显式跑一遍。
- 写作约定：ADR-0059 §5 自查在 PR 描述「文档」节逐项勾选（推荐名、规则句强度词、
  SOP 一步一动作、警告前置、状态变更带「预期：」、检查步骤有查法）。
- 命令模板已在现机核验：扫描工具两份安装的版本（V2.0.9 / V2.1.0）、`xlrd` / `xlwt`、
  `soffice`、`/mnt/jxtinno-sonic` 与 `/mnt/stp-log`、`/mnt/stp-aee` 挂载点。

## Revisit

- run 1264（P535AA）结束并补扫新增事件后，按 SKILL.md 重跑，核对「快照口径」是否仍适用。
- 若该流程被平台 API 化（导出 manifest、控制面导出、统一计数服务），把 SOP 收敛为对 API
  的调用说明并删除手工命令模板；`scripts/` 中的参考实现按归档约定处理。
- 每批次实跑出现新踩坑时，按「实跑 → 改文 → 同 PR 追加校准记录」更新踩坑守卫，不靠推演。
