# 多站点 P1/I2：发布清单消费与脱敏 plan

Status: implemented
Class: feature

## Decision

沿用 [ADR-0041](../../adr/ADR-0041-independent-site-delivery-and-management.md) 的独立站点边界，落实 [P1 设计](../../design/2026-09-multi-site-installation.md)的 I2（§3/§4/§6，§8 内网制品通道行）；不扩张为发布端或安装器。

- 新增**消费端**发布清单契约：`site.yaml` 的 `release.manifest` 声明本地清单路径（`validate` 允许空，`plan` 必填）。清单为 ≤1 MiB 的 JSON，拒绝 symlink/目录/超大/非 UTF-8 与重复键；字段含 `product.version`（须等于 `expected_release`）、`source.revision`、`components[]`（`sha256:<64hex>`，至少 `agent-code` 与 `host-resources`，对齐 ADR-0040，不造竞争算法）、`database.schema_target`、`compatibility`（agent 协议范围 + 逐发行版版本/架构矩阵）与 `provenance.attestation`（`signature` 或 `controlled_channel` + 绑定名）。
- `plan` fail-closed：配置或清单不合规、缺少必需组件或来源声明、版本不一致、任一角色 OS/CPU 不在支持矩阵 → `FAIL`（退出码 1）。输出脱敏：只含字段名/数量/模板名与“待绑定”标记，不含输入值、目标、路径或绑定名。`--save-dir` 只接受已存在、非 symlink、属主为当前用户的目录；`0600` 一次性写 `plan-report.json`，已存在即拒绝（无 `--overwrite`）。
- 摘要只证明完整性，**不证明来源可信**：验签、真实文件哈希、渠道授权核验与包体打开留给发布端与安装阶段，`plan` 报告显式输出 `release.provenance=BLOCKED`、`attestation_verified=false`。
- 模板参数化：`stability-platform-https.conf` 的域名与证书路径改为 `<server-name>`/`<tls-cert-path>`/`<tls-key-path>`；`tools/verify_control_plane_templates.py` 增加“必须含三占位符、禁写死域名与 letsencrypt 路径”的不变量；清单与演练 runbook 的渲染函数与残留自检（`grep -qE '<[a-z][a-z-]*>'`）同步。
- 本切片独立登记为 `multi-site-release-plan`（issue #1989，`test-impact=direct`）。

## Alternatives

- 清单路径按 bundle 同目录隐式推导：拒绝——隐式耦合且 plan 需要打开包体；改为显式字段。
- 在 plan 阶段验签/校验真实哈希：缺少信任根与包体读取权限，且会把“声明存在”误报为“来源可信”；交发布端与安装阶段，报告保持 BLOCKED。
- plan 输出真实目标/路径便于排查：与 I1 的脱敏口径冲突；改用字段路径与角色定位。
- `--save-dir` 允许覆盖既存报告：会掩盖上一轮计划证据；保持一次性写入。

## Verification

- `.venv/bin/python -m pytest tests/test_site_config.py tests/test_site_config_plan.py backend/tests/test_deployment_files.py -q`：**236 passed**（I1 188 / I2 新增 30 / 部署文件 18）。I2 用例覆盖：两组合成站点共用同一模板渲染且互不串值、清单缺失/不可读/重复键/未知字段/坏摘要/缺组件/缺来源声明/版本不符/平台不在矩阵的 fail-closed、`PRIVATE_MARKER` 脱敏、`--save-dir` 四类保护（缺失/symlink/非目录/已存在）、无网络/子进程/隐式读写审计。
- `.venv/bin/python -m tools.verify_control_plane_templates`：通过（新占位符不变量生效）。
- `.venv/bin/python -m ruff check backend/ tools/ scripts/`：通过（门禁同口径）。
- `.venv/bin/python scripts/run_gates.py check:quick`：**10 gates OK**。
- 仓库样例 `deploy/sites/site.example.yaml` 的 `plan` 预期退出 `1`（未填配置先被拒）；合成站点 `plan --json` 输出 `PASS`，`template_changes` 含 HTTPS 四项占位符（两项 `resolved=false`）、`steps` 为角色级计数。
- 说明：`tests/test_check_exemption_protobuf_814_746.py` 在 origin/main 上存在 2 处既存 `F841`；该目录不在 ruff 门禁范围（CI 与 `check:quick` 均为 `backend/ tools/ scripts/`），与本切片无关，未顺手修改。
- **pending（功能验收）**：I3–I5、远端预检、真实安装/迁移/管理员引导/Agent 接入/真机主链、导航与 MS-01–MS-15；`plan` 不构成安装通过或来源已验证。

## Revisit

- 发布端（R2）落地时对齐本清单契约并补齐验签/渠道留痕；若字段需要变化，先改契约与测试，不让生产端与消费端分叉。
- I3 起在获授权的目标上下文内执行真实哈希/签名验证与安装编排；不得把 plan 的 PASS 继承为安装证据。
- 若 B 站出现新的发行版/架构/协议域，先扩矩阵与有限契约，不关闭检查或自动降级。
