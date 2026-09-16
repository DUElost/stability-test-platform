# #2269 build_bundle 不再打包构建机本地状态（.env / 字节码），并加双层 fail-closed

Status: implemented
Class: bug-fix

## Decision

三处改动，共同消除「整树复制把构建机本地状态带进交付物」：

1. **构建侧排除**（`tools/release/build_bundle.py`）：`_copy_tree` 的 `copytree` 加
   `ignore=BUNDLE_IGNORE`，排除 `.env*`（**模板除外**）与缓存/字节码；
2. **构建后自检**：`find_forbidden_bundle_entries(out)` 在 `_digests` **之后**运行，
   命中即 `BundleError("bundle_forbidden_entries")` fail-closed；
3. **站点侧 S0 断言**（`tools/site_config/install.py`）：`_forbidden_bundle_entries` →
   `install.s0.hygiene` FAIL。判据**复用构建侧同一函数**，避免两处漂移。

## 缺陷确认（回源码，非仅采信 issue）

issue 的五条证据经复核**全部属实**：

| # | 证据 | 复核 |
|---|---|---|
| 1 | `TREE_LAYOUT` 含 `backend`，`_copy_tree` 无 `ignore=` | ✅ `build_bundle.py:26,151`；原 `copytree(...)` 无 ignore |
| 2 | `deploy/install.sh:103` 以仓库根为源 | ✅ 未改 |
| 3 | `backend/.env` 存在且 gitignored | ✅ 3,890 B、22 键；`git check-ignore` 命中 `.gitignore:27 .env*` |
| 4 | 部署态加载它 | ✅ `env_source.py:51-52`：先 `.env.backend`、再 `backend/.env` |
| 5 | 摘要面看不见 | ✅ `_digests` 只覆盖 `backend/agent` |

**我对证据 1 的补充观测（issue 未提）**：`backend/.env` 的 22 个键**不含** secret/token/password
一类命名（实测键名清单），危害**主要不是「凭据泄露」而是「配置继承」**——其中
`STP_FILE_SERVER_ADDRESS` / `STP_AEE_NFS_ROOT` / `STP_AEE_CIFS_ROOT` / `STP_SCRIPT_ROOT`
会让站点指向**构建机的**文件服务器与存储根。此点与 issue 的「未托管键被构建机覆盖语义」一致，
但把优先级从「密钥外泄」校正为「**跨站点错误指向**」。

## 实施中的两处自我纠正（留痕）

### 纠正 1：`.env.*` 模板豁免规则写窄了（**真 bug，被测试抓出**）

初版用 `name.endswith(".env.example")` 做豁免，`deploy/control-plane/env/.env.backend.example`
被误判为违规，导致 `tests/test_site_install.py` **40 例失败**。

进一步用 `git ls-files | grep -E '(^|/)\.env'` 查全仓实际形态，得到**8 个入库模板**：
`.env.server.example` / `.env.test.example` / `backend/.env.example` / `backend/agent/.env.example` /
`deploy/control-plane/env/.env.backend.example` / `…/.env.backend.internal.example` /
`deploy/postgres/.env.example` / `frontend/.env.example`——**全部以 `.example` 结尾**。

故规则改为 **`*.example` 一律豁免**（`_is_env_template`），并把测试从「只测 `.env.example`」
**加宽到三种真实写法**，防止再次写窄。

### 纠正 2：`shutil.ignore_patterns(".env.*")` 会吃掉模板

即使豁免判据写对，`ignore_patterns(".env.*")` 仍匹配 `.env.example`
（实测 `BUNDLE_IGNORE('backend', ['.env.example'])` 为 `True`）。
故 env 一族**不用 glob**，改自定义回调 `bundle_ignore()`：缓存按 glob、`.env*` 按
`_is_forbidden_env_file` 判据。

> 这两处若不修，会造成**比原缺陷更糟的后果**：把部署模板从交付物里删掉，站点安装无模板可用。

## 附带修复（同一根因的另一实例）

`_load_agent_digest_module` 经 `exec_module` 会在 **bundle 内**写出
`backend/agent/__pycache__`——即构建副产物被写进交付物。修复：加载期间
`sys.dont_write_bytecode = True`（`try/finally` 还原）。

该问题由既有用例 `test_rebuild_is_idempotent` **实际暴露**（第二次构建命中自检），
非我主动发现——属「自检起作用」的正例。

## Alternatives

- **只加 `ignore=`，不加构建后自检** → 否决：ignore 是**声明式**的，新增目录/改 TREE_LAYOUT
  即可绕过；自检是**结果式**的，能兜住前者漏掉的路径（本次纠正 1 即为实例）。
- **只在 S0 拦截，不在构建侧排除** → 否决：那样交付物**仍含**凭据（已分发出去），
  S0 只是拒绝安装；构建侧排除才是**让错误状态不产生**。两层都要。
- **站点侧自写一套判据** → 否决：两套判据必然漂移（一侧放宽即静默放行）。
  改为**导入构建侧同一函数**（`from tools.release.build_bundle import find_forbidden_bundle_entries`），
  并保留一个保守退化路径（导入失败时只拦 `.env` / `__pycache__`，**不放行**）。
- **正向白名单收集（只打包需要的文件）**（issue 建议 1 的备选） → 本版未采纳：
  改动面远大于本单（需枚举全树所需文件，回归风险高）。已记入 Revisit。
- **把 `backend/.env` 从 S2 落地中排除** → 否决：治标——问题的产生在构建侧，
  且其他构建机文件（字节码/缓存）同样不该分发。

## Verification

- `pytest tests/test_release_bundle.py tests/test_site_install.py tests/test_deploy_scripts.py -q`
  → **102 passed**；
- **红绿双向（构建侧）**：还原为「无 ignore + 无自检 + 无 `dont_write_bytecode`」→
  4 个新用例**全部红灯**；修复后 **22 passed**；
- **红绿双向（站点侧）**：注释掉 S0 断言 → `test_bundle_carrying_dotenv_fails_closed_at_s0`
  **红灯**、负向对照仍绿；修复后 **2 passed**；
- **模板豁免实测**：`_is_forbidden_env_file` 对 8 个真实模板名**全部 False**、
  对 `.env` / `.env.local` / `.env.backend` / `.env.production`**全部 True**；
- `ruff check` 两文件 → All checks passed；
- `check_governance_surface.py --check` → S1–S14、S5x 全绿；`check_layering.py` → OK；
  `env_inventory` gate → OK；
- `check:quick` 的 **eslint 在本 worktree 无 `node_modules`**（已知环境缺口，非本改动引入）；
  已在主检出 (`npm --prefix frontend run lint`) 确认基线通过——本 PR 为纯 Python 改动。

## Revisit

- **正向白名单收集**（issue 建议 1 的备选）：本版用「黑名单 + 自检」；若后续再出现
  「构建机文件混入交付物」的新实例，应升级为**白名单**（只收集清单内文件），
  从根上消除「漏排一类文件」的可能。
- **`backend/.env` 的键是否应被托管**：本单只解决「不扩散」。若站点确需其中某些键，
  按 issue 建议 3 走 `MANAGED_ENV_KEYS` 显式声明通道——**不在本单范围**。
- **构建机既有 release 产物**：本单阻止**今后**的构建携带 `.env`；**已分发**的历史产物
  需另行评估（是否有站点已继承构建机配置），本单不做追溯清理。
