# ADR-0040 D8 R4a：wrapper 删除资源子命令 + ADR-0037 白名单回填（2026-09-26）

Status: implemented
Class: feature

## Decision

按 ADR-0040 v1.2 D8 的 R1–R4 顺序（2026-09-26 owner 裁决），R4 拆两片，本片（a）处理提权面：

- **删除 `apply-resources` 子命令**：包括实现、解析器、`_SUBCOMMAND_CONTRACT` 条目和分发表。R1（#3437）起远端脚本
  已不再调用它，能力判据也不再要求它。
- **删除 `write-digest --kind`**：原 `--kind resources` 会写第二身份文件 `ARTIFACT_DIGEST_RESOURCES`。现在
  `write-digest` 只写 `ARTIFACT_DIGEST`；输出去掉 `kind=` 字段（控制面不解析该行）。
- **保留**：`PROTECT_ONLY_PATHS = ["resources/***"]`、`HOST_LOCAL_PATHS = ["resources/mtbf/"]`，以及
  `PROTECT_ONLY_METADATA` 中的 `ARTIFACT_DIGEST_RESOURCES`。退役不做主机清理，主机上的存量资源与记号原样保留。
- **ADR-0037 回填（ADR-0040 §7-5）**：核对发现，D2 白名单**从未**列入 `apply-resources`——#1975 P2-B 新增它时没有
  按 §7-5 同 PR 回填。本片删除之后，白名单与实现重新一致，D2 段落内加注回填记录（事实更正，不升版本）。

结果：提权面收窄，Agent 用户再也无法经 wrapper 往 `agent/resources/` 同步内容，也无法写第二身份文件。

## Alternatives

- **保留 `apply-resources` 作为兼容 no-op**：R1 之前的控制面遇到资源漂移时，仍会接着调用 `write-digest --kind resources`，
  兼容层得把两处都做成 no-op 才不出半态。为一个过渡窗口增加提权面代码并不值得，改由「上线顺序」约束解决（见 Revisit）；弃。
- **`--kind` 只保留 `code` 一个选项**：没有调用方传 `--kind`（远端脚本不带它，Ansible 用 copy 模块写文件），
  留一个单值选项只会成为死参数；弃。

## Verification

- wrapper 相关 8 个测试文件 **116 passed**，覆盖解析契约、selftest 契约校验、write-digest、apply-code 保护、
  flash 原语、PATH shim 沙箱真跑远端脚本和 Ansible 排除集契约
- **旧 wrapper 反证**：把 `stp_agent_priv.py` 换回 origin/main 版本后，两条新守卫都红：
  「`apply-resources` 与 `write-digest --kind` 被解析器拒绝」、「即便带着 kind 也只写 ARTIFACT_DIGEST」
- #2011 接线元测试改用仍在契约内的 `apply-code` 复刻缺陷形态，校验器依旧能报出
- `check:quick` **16 gates OK**；`tests/` 全量 **1916 passed / 18 skipped**

## Revisit

- **上线顺序约束**：新 wrapper 经 `update_agent.yml` 装到主机**之前**，控制面必须已部署含 R1（#3437）的 rev。
  R1 之前的控制面在能力判据里要求 `apply-resources`，遇到新 wrapper 会在任何写动作之前 fail-closed 并给出指引，
  不会留下半态，但在控制面升级前，这些主机的热更新会被挡住。截至本 PR，生产 `current` 仍是 R1 之前的 rev。
- R4b：Agent 心跳停报资源 digest、`version_info` 与契约枚举退役 `resources` kind、心跳路由停写
  `host.agent_resources_digest`、API / 前端字段标弃用、台账 `host-resources-layer` 结项。
