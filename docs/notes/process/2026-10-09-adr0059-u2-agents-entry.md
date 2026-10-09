# ADR-0059 U2：AGENTS.md 写作约定入口（#3622）

Status: implemented
Class: process

## Decision

按 ADR-0059 D5-3 / §5 U2，在 `AGENTS.md`「提交前」节 Agent Note 那一行末尾追加一句规则：
契约类文档与 SOP 中新写或改动的句子，作者必须按 `docs/development/writing-conventions.md`
书写（ADR-0059）。只读 `AGENTS.md` 的 Harness（OpenCode、Zcode、CodeBuddy CLI、dsh）由此看到约定；
Claude Code / Codex / Cursor 另有 U1 的 `contract-sop-writing` skill。

## Alternatives

- 在「按需入口」表加一行：会把 `AGENTS.md` 推到 80/80 行（S6 预算上限），不选。
- 只写链接、不写强度词：新句子属契约类，按 D3 必须写出执行者和强度词，故写成「作者必须按……书写」。
- 不改 `CLAUDE.md`：它是指向 `AGENTS.md` 的 symlink，内容随之更新。

## Verification

- `AGENTS.md`：79 行不变，6695 → 6841 字节（S6 预算 80 行 / 8000 字节）；硬不变量节与 S11 锚定原文未动。
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：退出码 0，16 个门禁通过（本容器的隔离 `.venv`）；
  `schema-at-head` 因未设置 `DATABASE_URL` 按门禁设计跳过，未连接任何数据库。
- 只读 `AGENTS.md` 的 Harness 是否在写契约 / SOP 时读取约定：UNVERIFIED（本容器只有 Claude Code CLI）。

## Revisit

- ADR-0059 §6：只读 `AGENTS.md` 的 Harness 长期只有 UNVERIFIED 时，复议 D5 的入口设计。
- `AGENTS.md` 再次逼近 S6 预算时，优先压缩措辞，不为本句另开行。
