# 过渡登记簿：给「标注为过渡」装上到期时钟（2026-09-24，ADR-0051 D8 落地）

Status: implemented
Class: architecture

## Decision

AGENTS.md 总原则要求「临时止血必须标注为过渡并写明终态出口——不标注的止血会沉淀为技术债」，
但这句话此前**没有任何机检**：docs 里 448 处「过渡/止血」措辞混杂着历史陈述与在途机制，
真正会腐化的机制性过渡（env 注入、回退双轨、逃生阀窗口）没有到期回收路径。ADR-0051 D8
点名补这个缺口，本单落地：

- **唯一台账** `docs/governance/transitions.json`：条目恰好
  `{id, what, exit, due, status, evidence?}`；`status ∈ {active, done, dropped}`；
  首批登记 ADR-0051 链上全部 6 个在途过渡（flash env 注入、Unisoc 四路径键、
  SCRIPT_PACKAGES off|on 模式、#3075 C3 回退窗口、发布根 env 挂靠检出、两行误建 Unisoc script 行）。
- **门禁** `tools/dev/check_transitions.py`（stdlib-only）：lint + **active 过期即红**
  （续期合法但必须走 PR 改 due 并在 what 写理由——把「忘了」变成「当着评审改日期」）+
  **exit 锚可解析**（`adr:ADR-NNNN#条款` 文件/锚点在场、`issue:#N` 格式合法；
  禁止"以后再说"式空出口）+ done/dropped 必须带 evidence。机器不验证"对象确已删除"——
  删除 PR 会让对象自己的在场判据变红，那是既有机制，登记簿只保证**没人忘**。
- 接线三处：`run_gates` GATES + check:quick/check:pr profiles（16 gates）+ CI step
  「过渡登记簿检查(ADR-0051)」+ S5x 锚配对。
- 台账只收在途机制性过渡；docs 历史面「曾为过渡」的陈述不追改不入账。

## Alternatives

- **markdown 台账 + 人读**：弃——不可机检等于没有；json 让 lint 判据（字段恰好、词表、
  日期格式）与 append-only 风格登记面同构。
- **扫 docs 全文的「过渡」措辞自动生成台账**：弃——448 处措辞大多是对历史的陈述，
  自动生成会把历史面拖进执法面；登记动作本身是承诺，必须显式。
- **gate 连 GitHub 验证 issue 闭合**：弃——CI 无稳定出口（已知抖动），且「出口锚在场」
  已足够防遗忘；闭合回收由 due 到期逼 PR。

## Verification

- `check_transitions --self-test` 红绿双向（schema 六违例、到期红/未到期绿、exit 三类锚、evidence 两向）；
- `tests/test_transitions_registry.py` 3 passed：接线钉桩 + 真实台账形状 + **到期执法有牙**
  （把真实条目 due 改到过去必须红——不是摆设自证）；
- 真实台账 `--check` 绿（6 条在途）；`check:quick` → **16 gates OK**；ruff OK；
  治理守卫 S1–S15 + S5x OK（gate 命名/CI 锚配对同 PR 落地）。

## Revisit

- `unisoc-miscreated-script-rows` due 2026-09-30：软退役两行后即转 done（evidence=PR 号）。
- `script-packages-off-on-modes` / `unisoc-env-path-keys` / `dedup-scan-env-fallback-window`
  due 2026-10-31：Phase 4b/5 收口时逐条撤账。
- 若登记簿被证明有效，AGENTS.md 总原则可加一句「机制性过渡必须入账 `docs/governance/transitions.json`」
  ——那是 S11 锚改动，随后续单评估，本单不动共享元文件。
