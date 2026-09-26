# script-versioning.md「新版本上线收尾」节对齐 ADR-0051 Phase 3 包模型（#3276）

Status: implemented
Class: bug-fix

关联：[#3276](https://github.com/DUElost/stability-test-platform/issues/3276)（09-25 七天
文档漂移审计立单）、[ADR-0051](https://github.com/DUElost/stability-test-platform/blob/main/docs/adr/ADR-0051-release-unit-and-content-addressing.md)
Phase 3（`64d0ef93` 删 210 版本目录 / `agent-code` 排除 `scripts/`）、#3379（`--publish`
原子落位）、#735/#3075（关联 origin）。

## Decision

- 整节按包模型重写：收尾链从「新增版本目录 → scan → canary hot-update →
  `batch_hot_update` 放量」改为「**改族树 → `--register` → `--publish` → scan →
  重指 plan_step → 单台 Plan 验证后放量**」，并明写「脚本不再随 hot-update 分发，
  没有热更新步骤」。
- 保留仍成立的步骤原文：模板钉钉守卫（#2998）与 `EXCEPTIONS` 清账规则、
  `_validate_script_refs` 的 pin 校验、`plan_step` 重指、`script-presence` `missing=0`
  的账本盲区警示（#3111）。
- 判到位口径换到新模型：`verify_scripts` 预热后的 `tools_cache` 验证标记计数 +
  `step_trace` 无 `script_verify_failed`（替代旧「逐台 `agent_code_sync_status=matched`」）。

## Alternatives

- **整节删除、只留头部「发新版本」链**：弃——模板钉钉与重指次序是本节独有的收尾
  语义（头部只覆盖登记/发包/scan 三步），删节会丢 #2998/#3111 两条实测教训。
- **等 #3205 描述面批一起改**：弃——本节是把执行者引向已废止交付路径的**行为性**
  指挥（P1），与 #3205 的描述面时态/计数口径不同层。

## Verification

- 事实面逐条对照代码：`--publish` 原子落位（#3379）、scan 输入 = manifest + 站点包源
  （`package_missing` 只报告）、`verify_scripts` precheck/presence 预热（同文件 D7 节
  与 Phase 2b 实测记录）、`agent-code` 载荷排除 `scripts/`（`host_updater._TAR_EXCLUDES`）；
- 全文件 grep 旧模型词（`v<ver>` / 全量副本 / `batch_hot_update` / `nfs_path` / 本地树执行）
  零残留（唯一命中是新写的「不再分发」说明句）；
- `python scripts/run_gates.py check:quick` 全绿（docs-only，门禁含治理面与锚点检查）。

## Revisit

- §2/§3 引用 control-plane-deploy SOP 的小节号若后续 SOP 改版需同步（纯指针，无语义锚）。
- #3278（SOP/skills 旧模型批）与本节同主题不同文件，另行收口。
