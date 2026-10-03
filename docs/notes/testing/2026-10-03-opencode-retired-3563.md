# OpenCode CLI 退出 Harness 探针验收矩阵（#3563 / #3516）

Status: implemented
Class: testing

## Decision

Owner 2026-10-03 裁决「OpenCode 退役」。按 2026-10-01 退役 Cursor IDE / CodeBuddy IDE 的同一先例落地：
**退出验收矩阵**。`tools/dev/harness_probe.py` 不再登记 `opencode` 形态，`--only opencode` 与任何未知
形态一样被拒绝（`run_matrix` 返回 1，不启动任何会话）；操作手册 `harness-probes.md` 新增「已退役形态」，
并把“6 个 CLI 形态”改为 5 个。

依据（[#3563 报告](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5966758919)，
revision `5e0b6b1f`）：OpenCode 在 3 个 cwd 上共 12 次探针调用全部没有有效答复。宿主默认模型每次调用
不同——预检时是可用的免费模型，探针首轮落在 `deepseek-chat`（无可用渠道），重试 9 次均为
`gpt-5.6-luna`（需有效订阅）。它没有走向 PASS 的路径；而 v1.3 的 UNVERIFIED 接受范围只含
Codex CLI / Cursor CLI，“其它在册形态不享有此豁免”。

**不做**：不删 `harness-adapters.md` 的 OpenCode 适配行（OpenCode 仍可经 `AGENTS.md` 读取共享契约，该行
不含验收声明；该文件已贴 S6 预算，且另有在途 draft #3597 串行占用）；不动 `.gitignore` 的 `opencode.json`
与 `production-diagnostics.md` 的本地态清单；不改写 ADR-0034 附录 A 与各历史 Note。

## Alternatives

- **修宿主默认模型后重跑**：默认模型属个人配置，不受仓库控制且会轮换。改一次配置只得到一次“当时可用”的
  证据，不能成为可复现的验收格；探针也不应为此指定模型（会让命令偏离用户的默认体验）。
- **按 Codex / Cursor 同口径接受 UNVERIFIED**：扩大 v1.3 的豁免范围，并留下一个永远 UNVERIFIED、没有转
  PASS 路径的在册形态。
- **保留并让它长期 UNVERIFIED**：矩阵里恒有一列不可判，掩盖“哪些形态真的有证据”。
- **改探针去适配 OpenCode 的模型选择**：把宿主配置问题变成探针特例，违背“探针命令不随宿主调整”。

## Verification

- 新增 `test_retired_opencode_cli_is_gone_and_rejected`：断言 `opencode` 不在 `FORMS`；并把
  `subprocess.run` 换成会抛错的桩后，`run_matrix("opencode", 1, None) == 1`——请求被拒绝且**不会启动任何
  外部会话**。
- **变异自证**：把 `opencode` 形态加回 `FORMS`，该测试变红（1 failed / 61 passed）；恢复后文件与原版逐字
  一致，62 passed。
- `tests/test_harness_probe.py`：**62 passed**（项目 Python + `scripts/run_pytest.py` 保护入口，6 GiB / swap=0）。
- 两个改动的 Python 文件 Ruff 通过。`check:quick`：**16 gates OK**（未配置 `DATABASE_URL` 的 schema 对齐检查
  明确跳过，不算数据库验证）。首次因新 worktree 未链接前端依赖而在 eslint 处失败，链接后重跑通过，与改动无关。
- 根目录完整离线测试：**2298 passed / 18 skipped**（438s，同一保护入口）。
- required CI 另行判定，本地结果不替代它；独立复核 pending。

## Revisit

- 若 OpenCode 之后成为被支持的 Harness 且默认模型稳定可用，按**新证据**重新登记，不恢复本次的旧配置。
- `harness-adapters.md` 的 OpenCode 适配行保持不变；待 #3597 合入、G4 贴顶文档迁移时再统一审视该文件。
- 同批观察、**未在本变更处理**：`harness-probes.md`「已知不可跑形态」仍写 Cursor CLI 恒 UNVERIFIED 为接受
  的终态，但 2026-10-03 换账号后已有六格 PASS（[#3563](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5965565067)）；
  该叙述属 #3516 G4 的陈旧叙述收口范围。
- 探针目前把 `system/api_retry` 瞬时重试一律判为 UNVERIFIED；是否区分它与真正的工具错误是另一项判卷语义
  决策，不在本变更内。
