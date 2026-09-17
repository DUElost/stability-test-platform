# scan 反激活加「子树 = 部署目标」守卫：读了非权威树时拒绝退役（#2386 验收第 3 条）

Status: implemented
Class: bug-fix

- 日期：2026-09-17
- 相关：`#2386`（本单；可见性那半由 PR #2405 交付）、`#1987`（同根因面：共享工作树被多方当工作区）、
  `ADR-0039`（D1/D2：退役是可逆的自动化、删除只能人工）、`ADR-0020`（版本不可变）

## Decision

### 1. 不翻转「盘上缺失 → 反激活」的全局语义，把闸门加在**输入态**上

本单原文的备选修法 3 是「把『磁盘缺失』从直接反激活改为报告 + 需显式确认」。**不做**，理由是
两个方向的代价不对称：

- 反激活是**可逆**的（显式重激活即可，ADR-0039 D3 的冷却期正为此留），且 #2405 之后已经
  **可见**（响应明细 + 审计 + WARNING + runbook 前置）；
- 反过来若「缺失不反激活」，库里会留下**目录已不存在但 `is_active=true`** 的行 → 计划仍可 pin 到它
  → 到派发/运行时才发现脚本没了。**这个窗口比误退役更坏**，且 ADR-0039 D1/D2 明确建立在其上
  （「可逆的动作（退役 `is_active=false`）才允许自动化」）。

真因不是语义，而是**读了非权威树**：`STP_SCRIPT_ROOT` 生产上就是共享主工作树，别的会话切走分支后
窗口内跑一次 scan 就会退役主线版本。所以守卫加在这里：

```
被扫子树 == origin/main  → 反激活照常（语义不变）
被扫子树 != origin/main  → **不反激活**，把「本会反激活」的清单放进
                          deactivation_skipped_versions（进响应与 scan 审计）
被扫子树不可判定          → fail-open（照常反激活）+ 说明
```

判据落在新函数 `script_tree_matches_deploy_target(root, base="origin/main")`：
`git rev-parse --show-toplevel` 定位仓库根 → `git diff --quiet origin/main -- <脚本子树>`。
**与执行者一直手工做的那条命令同源**（#2405 把「先 `git diff --quiet origin/main -- backend/agent/scripts`」
写进 runbook 的肌肉记忆），本次只是把它工具化。

### 2. `allow_deactivate=true` 是那条显式出口

确要在非主线树上退役时显式传该 query 参数（沿用 `force_rebaseline` 的形态）。它出现在
scan 的审计里（`details.to_dict()` 带 `deactivation_skipped_versions`），所以「谁在什么时候
绕过了守卫」可回溯。**通道存在的意义是「不必为此切树」，不是「让守卫可忽略」**——故缺省为 false。

### 3. fail-open 的边界（刻意的）

判据返回 `None`（不在 git 仓库内 / 无 `origin/main` / git 不可用）时**照常反激活**：
发布包是不可变发布物，不存在「切分支」场景；把「判据不可用」当成「不一致」会让一次正常部署被拒。
代价是发布包场景没有这层保护——由 §1.4 的 `check-deploy-source.sh` 前置承担。

## Alternatives

- **翻转全局语义（原文修法 3）**：见 §1，会引入「活跃但盘上不存在」的窗口。否。
- **scan 一律拒绝（连新增/刷新也不做）**：把「读了旧树」升级成「部署被挡」，而新增/刷新/报冲突
  在旧树上是无害的（它们不删不改已发布目录）。否。
- **把守卫放进 API 层（`scripts.py` 里判树）**：判据与扫描输入强耦合（根就来自 `STP_SCRIPT_ROOT`），
  放服务层才能被单测直接覆盖、也才能让 `scan_script_root` 的其它调用方共享。否。
- **只在 runbook 里加前置、代码不设防**：#2405 已经加过，而窗口是**常态**（同一会话内被切过 5 次），
  靠人记住在正确时机跑那条命令正是本单要消灭的。否。

## Verification

- `backend/tests/services/test_script_catalog_activation.py` → **9 passed**（新增 4 条）：
  1. **非部署目标树 → 不反激活**，行仍 `is_active=True`、`deactivated == 0`，且
     `deactivation_skipped_versions` 点名了「本会退役」的版本（= 本单验收第 3 条）；
  2. `allow_deactivate=True` → 照常反激活，且**不再花那次 git 判定**（显式开关下无需探测）；
  3. 判据不可得（tmp_path 非 git 树）→ **fail-open** 照常反激活；
  4. 判据本体（真 git 仓库）：子树与 `origin/main` 一致 → `True`；改一个字节 → `False`。
- **红向反证**：把 `guard_blocks` 置为恒 `False` → 第 1 条**红**（1 failed / 8 passed）；还原 9 passed。
- `backend/tests/api/test_scripts.py`（含 scan 端到端）→ **30 passed**；
  `backend/tests/services -k script_catalog` → **38 passed**。
- `run_gates check:quick` → **10 gates 绿**；`ruff check` 全绿。
- **一条既有红（与本单无关）**：`tests/test_script_seed_governance.py::test_new_seed_migrations_deactivating_versions_check_references`
  在 `origin/main` 上即红——#2399 的迁移 `e5f6a7b8c9d0_repair_flash_firmware_seed_identity_2399.py`
  停用版本但未带引用检查（#2055 门禁抓到了它）。我的 diff 不含 `backend/alembic/`，未触碰。

## Revisit

- **`origin/main` 的时效**：判据比的是**本地** `origin/main`。若本地未 fetch（落后），子树会与
  陈旧目标不一致 → 守卫**偏保守**（拒绝反激活），方向安全；但若有人期待「本地 main 就是目标」，
  需在 runbook 里保留 §1.1 的 `git pull` 前置（现状如此）。
- **API 层的返回形状**：`deactivation_skipped_versions` 是**新增键**（`to_dict` 与响应同时可见）。
  消费方（runbook 的 jq 投影已同步）无需改动即可忽略它，但看板/脚本若要区分「退役了」与
  「被守卫拦下」，应读这个键而不是 `deactivated` 计数。
- **#1987 的终态**（scan 输入换成「已校验 revision 的只读检出」）仍是更彻底的解，属 ADR 裁决面；
  本单是它落地前的确定性防线。
