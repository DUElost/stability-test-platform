# env_inventory 漏扫 backend/scripts/**：跳过判据收窄到 agent 侧子树（#2026）

Status: implemented
Class: bug-fix

## Decision

`tools/dev/env_inventory.py` 的跳过判据由「任意层级的 `scripts` 组件」收窄为
「agent 侧子树前缀」，两件事解耦：

```python
SCAN_SKIP_PARTS = {"__pycache__"}          # 相对 SCAN_ROOT 的组件
SCAN_SKIP_TREES = (("agent", "scripts"),)  # 相对 SCAN_ROOT 的子树前缀
```

原实现 `SCAN_SKIP_PARTS = {"__pycache__", "scripts"}` 的组件是**相对 `SCAN_ROOT`
（`backend/`）**取的，于是 `backend/agent/scripts/**`（有意排除）与
`backend/scripts/**`（控制面脚本目录，21 个文件）一起被跳过——门禁仍报 OK，而本窗口
新增的凭据变量 `STP_INITIAL_ADMIN_*` 对门禁不可见；更糟的是 `audit()` 的「声明陈旧」
分支会据此**建议删除**只在该目录读取的声明（漏扫主动产生错误清理建议）。

修好后首次进入清单的是 8 个读取名，全部**声明为内部**（`.env*.example` 未登记）：

| 名称 | 判为内部的理由 |
|---|---|
| `STP_INITIAL_ADMIN_USER` / `STP_INITIAL_ADMIN_PASSWORD` | 站点安装链 S3 受控首管理员引导的一次性入参（`tools/site_config` 以子进程环境注入，密码只经环境）。**刻意不进 `.env` 模板**——示例文件是运维模板，登记会诱导把一次性引导凭据写进常驻 env 文件 |
| `STP_AUDIT_BACKEND` / `STP_AUDIT_ENV_FILE` | 一次性诊断脚本（`audit_stage_a_env` / `preflight_control_plane`）的 `--backend` / `--env-file` 等价项；无内置默认地址是有意的（不硬编码生产地址） |
| `STP_BACKEND_URL` | 一次性运维脚本（`batch_hot_update`）的控制面地址覆盖，默认本机 `127.0.0.1:8000` |
| `STP_EXTRACT_BACKEND` | 一次性提取脚本（`jira_extract_run52`）的 `--backend` 等价项，无内置默认 |
| `STP_SMOKE_HOST_ID` / `STP_VERIFY_BACKEND` | 真机冒烟脚本（`sprint4_real_device_verify` / `smoke_jira_api`）的 `--host-id` / `--backend` 等价项，无内置默认 |

生成清单 210 → **218** 个读取名；文档生成块按 `--write` 刷新（未手改）。

## Alternatives

- **A. 把 `SCAN_SKIP_PARTS` 整体改成相对 `ROOT` 的元组**：不做。前序 Note
  [`2026-09-14-env-gates-dotwt-worktree-1978.md`](2026-09-14-env-gates-dotwt-worktree-1978.md)
  §Revisit 明确警告过「`scripts` 是相对扫描根比较的，不要一并改成相对 `ROOT`」——
  本次保持两个常量的比较基（`SCAN_ROOT`）不变，只把「组件命中」换成「子树前缀命中」，
  与该警告不冲突；判据仍按组件序列比较，与 #1978 的 `.wt` 教训同源。
- **B. 只删 `scripts` 组件、不给 agent 侧留排除**：否决。会把
  `backend/agent/scripts/**`（ADR-0020 冻结的版本化脚本目录）的读取名拉进清单——
  它们不是控制面配置面，且该目录内容冻结、改不得。
- **C. 把 8 个名字登记进 `.env.example`**：否决（理由见上表，`STP_INITIAL_ADMIN_*`
  尤甚）；其余 6 个是 CLI 等价项，登记会造成「部署旋钮」的误解。
- **D. 只修判据、不补声明（让门禁红着以示破窗）**：否决。门禁的设计是「登记 ∪ 内部
  声明」二选一，无解释的红会逼下一个人加白名单，反而固化问题。

## Verification

- **红**：只改判据、未补声明时 `--check` 报出 8 条「未登记进示例、也未声明内部」，
  与 issue 列举的名字**逐字一致**（`STP_AUDIT_BACKEND`、`STP_AUDIT_ENV_FILE`、
  `STP_BACKEND_URL`、`STP_EXTRACT_BACKEND`、`STP_INITIAL_ADMIN_PASSWORD`、
  `STP_INITIAL_ADMIN_USER`、`STP_SMOKE_HOST_ID`、`STP_VERIFY_BACKEND`）。
- **绿**：补 `_INTERNAL_ONLY` 声明后 `--check` 通过（218 个读取名）。
- **回归用例红绿双向**：新增 `tests/test_env_inventory.py::test_scan_covers_backend_scripts_dir`；
  在旧实现下 **失败**（`assert 'ZZ_CTRL_SCRIPT_VAR' in {}`——`scan_reads` 对
  「只有 scripts 目录」的夹具返回空集），新实现下全文件 **12 passed**。
  既有 `test_scan_skips_agent_scripts_catalog` 仍绿（agent 侧排除未被放开）。
- `python scripts/run_gates.py check:quick` → **OK (10 gates)**（含 env-inventory 门禁）。
- 机制后果（非手改，`--write` 生成）：`STP_ADMIN_USER` / `STP_ADMIN_PASSWORD` /
  `STP_SMOKE_ORIGIN` / `TEST_DATABASE_URL` 的首个读取点与类别随扫描面扩大而更新
  ——例如 `STP_ADMIN_*` 从「测试」翻为「运行时」（`backend/scripts/audit_stage_a_env.py`
  确有读取点）。这是清单**变准**，不是漂移。
- **未跑**：`check:pr` 与全量 pytest（改动面 = dev 工具 + 其测试 + 生成文档，无业务行为）。

## Revisit

- **类别语义变松**：`test_only` 的判据是「所有读取点都在 `backend/tests/`」。脚本侧一旦
  存在读取点，同名变量就从「测试」翻成「运行时」（本次 `STP_ADMIN_*` /
  `TEST_DATABASE_URL` 即如此），读者可能误以为它可用于生产。若将来要表达「只在测试
  用、生产禁设」，需要一条独立标记（现在是靠人读 `_INTERNAL_ONLY` 的理由文本）。
- **agent 侧排除仍是白名单形态**：`SCAN_SKIP_TREES` 目前只有一条。若将来还有「有意
  排除但不该按组件名全局跳」的子树，继续按子树前缀加条目，不要退回组件判定。
- **本批只覆盖环境变量清单面**：同族的「门禁静默漏扫」另见 #2035 / #2249（治理面
  S12/S14，另一 PR）；`backend/agent/scripts/**` 冻结目录的 6 处 ADR 版本引用豁免登记
  属 #2250，不在本单。
