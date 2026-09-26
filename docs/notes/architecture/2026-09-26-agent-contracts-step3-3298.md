# ADR-0054 第 3 步落地：`artifact_digest` 归契约包，算法单实现 + 格式锚

Status: implemented
Class: architecture

## Decision

按 ADR-0054 §5 第 3 步，把部署摘要实现收成**一份**（#3298）：

- `backend/agent/artifact_digest.py` → **`backend/agent/contracts/artifact_digest.py`**（git mv）。
  整个模块搬迁：算法面（`digest_entries` / `DIGEST_PREFIX` / kind 词表）与枚举面
  （`collect_artifact_entries` agent 载荷树、`collect_control_plane_entries` bundle 的
  `backend/**` 除 agent）。
- **控制面侧**：`backend/services/artifact_digest.py` 删掉本地 `digest_entries` 与
  kind 常量，改 `from backend.agent.contracts.artifact_digest import …`；控制面自己的
  输入集枚举（`host_updater._iter_payload_files`）**按 ADR 要求留在 services**，进程缓存
  与 `plan_convergence` 不动。
- **三个 stdlib 路径加载器**（不走 `backend.*` 包链的既有设计）同 PR 更新到契约路径：
  `tools/ansible/compute_deploy_digest.py`、`tools/release/build_bundle.py`（bundle 内副本）、
  `tools/site_config/install.py`（#2020 受信源 = 安装器自身源码树）。
- **测试**：`backend/tests/services/test_artifact_digest.py` 由「双侧镜像字节级对拍」改为
  ① 单实现断言（`ad.digest_entries is contract.digest_entries` + 旧路径不存在）；
  ② **枚举对拍**（控制面 tar 枚举 vs 契约树枚举，同一 fixture 树 → 同 digest——算法同源后
  仍会漂移的只剩输入集定义）；③ **新增格式锚 known-answer**
  （`test_digest_serialization_format_is_pinned`，含非 ASCII 路径与空集向量）。
  `tests/test_release_bundle.py` / `tests/test_site_install.py` 改指新路径；
  #2020 的「bundle 自带实现」攻击测试把诱饵放到**受信源同相对路径**
  （`backend/agent/contracts/artifact_digest.py`，并 mkdir 父目录）。
- **文档**：`check_payload_root_clean.py` 抬头路径、`DEPLOY.md` 契约清单、
  ADR-0051 的 digest 算法行（改「唯一实现 + 搬迁前双边镜像」双记录，不重写历史）、
  `tests/test_ansible_digest_contract.py` 抬头口径。

### 为什么新增「格式锚」（超出 ADR 字面，但由消融实验坐实）

去掉双边对拍后，**算法改动的可见性归零**：实测把契约里的
`separators=(",", ":")` 改成 `(", ", ": ")`，ansible parity 与控制面测试**全绿**
（两侧引用同一实现，改一起改，谁也不再是参照）。而 `digest_entries` 的序列化格式就是
部署身份本体——它同时决定主机 `ARTIFACT_DIGEST`、bundle manifest 与收敛判定，静默变化
会把 48 台机队整体判成 drift。故用固定向量把格式钉死；此后改格式必须伴随显式身份迁移。

## Alternatives

- **只搬算法、把 agent 树枚举留在 agent**：弃——三个路径加载器（Ansible / release build /
  site install）按同一文件同时使用枚举与算法，拆两个文件等于两次加载 + 跨文件协议，
  且会留下一个只有工具读取的 `backend/agent/artifact_digest.py`（D5 意义上的悬挂面）。
  契约包收「载荷输入集定义」与 ADR-0054 §2「路径与命名约定」同口径。
- **旧位置留再导出壳**：弃——D5 明令不留壳；4 个消费方（3 工具 + 2 根测试）同 PR 可改指。
- **不加格式锚，只在 Revisit 里记缺口**：弃——消融实验显示缺口是「静默改身份」而非
  「测试不好写」；一条 8 行的 known-answer 就能把风险从「机队 drift 才发现」提前到 PR 门禁。
- **把格式锚放在根 tests**：弃——它守的是契约模块行为，放 `backend/tests/services/`
  与既有 digest 测试同址，跑动成本最低。

## Verification

- `backend/agent/tests/` → **2160 passed**（4m08s，systemd-run 6G 硬顶）；
- 控制面 digest 相关：`backend/tests/services/test_artifact_digest.py`（14 例，含新格式锚）、
  `test_agent_version_info.py`、`api/test_heartbeat_artifact_digest_1907.py`、
  `api/test_hot_update_noop_gate_1907.py` → **33 passed**；
- 根 `tests/`：`test_ansible_digest_contract.py`、`test_release_bundle.py`、
  `test_site_install.py`、`test_agent_import_boundary.py`（C6 纯度已覆盖第 6 个契约模块）
  → **117 passed**；
- `layering`：5 合约全 KEPT（services → contracts 由通配行放行，C3 基线仍 2 条）；
  `inner-imports` 617/617（本步无函数体内 import 变化）；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (17 gates)**；
- **反向验证（临时变异，逐条复原）**：
  - 复活 `backend/agent/artifact_digest.py` → 单实现断言红；
  - 改契约序列化分隔符 → **加格式锚前全绿（坐实缺口）**，加锚后
    `test_digest_serialization_format_is_pinned` 红。

## Revisit

- ADR-0054 §5 只剩第 4 步（`kernel_usb_faults` 解析 + 签名词表、`aee/state_migration`），
  C3 剩余 2 条基线是它的出口；
- **digest 格式变更 = 身份迁移**：改 `digest_entries` 序列化、kind 语义或载荷排除集，
  必须同时走 hot-update + manifest/bundle 重算，属显式动作（格式锚会先红）；
- 三处载荷排除集（`host_updater._TAR_EXCLUDES` / Ansible `agent_install_excludes` /
  wrapper `FIXED_EXCLUDES`）仍由 `tests/test_ansible_digest_contract.py` 逐项锁定，
  契约包里 `PAYLOAD_EXCLUDES` 是其镜像副本——未在本步合并，若后续仍要收敛，另开一步；
- `contracts/artifact_digest.py` 里 `_RESOURCES_PREFIX` 常量当前无消费方（搬迁前即如此），
  本步不做行为无关清理；若契约包做一次 lint 收口，一并处理。
