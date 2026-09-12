# ADR-0038 独立评审交付（Codex / 182d4e）

Status: implemented
Class: process

## Decision

- 按 #1557 的 Mode C 提示词交付[独立报告](../../reviews/REVIEW_ADR0038_2026-09-12_182d4e.md)，覆盖 F1–F7；总评 Needs-revision，分为 4 阻断、3 建议、2 观察。这里的 implemented 仅指评审交付，不指 ADR 或退役功能已实施。
- 审查源码固定在 `b003b443314f1586bab5d4f0466569c715e102ca`，完成证据核验后交付分支更新到 `61e9e3b56de008307cd0d155a1826615af94fbfd`；不混用两者行号。只新增报告和本 note。
- 使用独立 worktree，排除主 checkout 的既有未提交改动；不读其他评审稿/评论，不改 ADR、代码或 issue 结论区。Registry 的同 issue 并行覆盖依据为用户指定的 #1557 Mode C 边界，仅共享协调元数据。
- PR 只引用 #1557，不关闭收集 issue，不自行 synthesis 或作出 Accepted 裁决。

## Alternatives

- 不直接改 ADR/代码：本轮授权为独立评审，方向裁决和实现另开。
- 不用一条“所有 Host 查询都过滤退役”概括修复：历史证据集合、迟到上传和只读诊断应与新执行/控制分开。
- 不用生产或本机业务库验证。纯身份用例关闭 fixture/插件，仅提供不可用测试地址满足惰性数据库模块导入；通知键验证通过 AST 提取纯函数，避免导入服务。
- 已按文件名检索基线 note，没有本次独立评审的既有 note；新建会话专属记录，不覆盖其他评审者的交付物。

## Verification

- `python -m pytest --noconftest -p no:cacheprovider -c /dev/null backend/tests/test_host_identity.py -q`：首次因缺 DATABASE_URL 收集失败；加报告列明的隔离环境后 **5 passed**，未连数据库。
- 通知去重键 AST-only 探针：两个函数均确认未区分 host 和退役周期；不等价于已验证未来退役告警。
- `python -m scripts.run_gates check:quick`：7 项通过；compileall 的既有 Jira key 正则 SyntaxWarning 未作为无关修复扩散范围。
- 97 处基线 file:line 结构校验、分级计数及 `git diff --cached --check` 通过；ADR/业务代码无差异，只有报告和本 note。
- `python -m tools.dev.ai_work drift --strict` 全局退出 1：存在其他 Execution 的 advisory 与目录级 overlap。本次 note 已显式补入 scope，复跑自身无非 overlap 提示；与另外 57 个 worktree 的文件名 diff 交叉验证无实际同路径冲突。不修改他人的登记，也不把全局 advisory 写成通过。
- 数据库回归、迁移、并发和 mutation 验收均 pending，留待实现单；未读取生产配置或执行设备操作。

## Revisit

- 人工裁决/另轮 synthesis 或 ADR v0.2 变更后，对准入退役竞态、历史收尾控制、身份继承及回滚边界定向复审。
- 新源码基线影响证据链时显式补审并标注提交，不用新分支的静态门禁替代旧基线的行为验证。
