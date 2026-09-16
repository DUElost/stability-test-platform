# apply-code 保护清单扩展 resources/（protect-only，P2 前置）（#1950）

Status: implemented
Class: feature

## Decision

落地 ADR-0040（Accepted v1.0）§4.3 硬前置——「分层切换前先把 `resources/`
加入保护清单」：控制面源树删除资源文件后，hot-update 的 `rsync --delete`
会把删除传播到所有 host 清掉 229MB 大件（aimonkey/flashtool 等）。现状
`HOST_LOCAL_PATHS = ["resources/mtbf/"]` 不设防。

**语义决策（protect-only，偏离 ADR 字面的「HOST_LOCAL_PATHS 增补」）**：
该列表每项生成 `--exclude` + `--filter=protect` 一对，直接增补 `resources/`
会立即**停掉 resources 的热更新分发**（exclude 生效），而 P2 的
host-resources 独立通道尚不存在——分发断档。ADR §4.3 的目标仅是「不误删」。
故：

- wrapper 新增 `PROTECT_ONLY_PATHS = ["resources/"]`：apply-code argv 仅追加
  `--filter=protect resources/`，不 exclude——分发照旧、删除被拦；P2 载荷
  收缩（agent-code 剔除 resources/）后分发自然停止，保护已在位；
- legacy（无 wrapper）远端 rsync 对称加 `--filter='protect resources/'`；
  > **#2019 后续（2026-09-16）**：该模式在 rsync 里只护**目录节点**，已改为 `resources/***`；
  > legacy 分支本身已由 #2180 退役（脚本侧零 filter）。见 `docs/notes/bug-fix/2026-09-16-protect-resources-tree-2019.md`。
- `resources/mtbf/` 维持 exclude+protect 不变（exclude 仍必需：载荷至今
  不携带 mtbf 内容；其 protect 语义被 `resources/` 传递覆盖）。

ADR-0037 §D2 已按 §7-5 同 PR 回填（子命令白名单不动——无新增子命令，
rsync 保护语义扩充成文）。

## Alternatives

- 直接 `HOST_LOCAL_PATHS` 增补 `resources/`（ADR 字面）——语义见上：分发
  断档先于替代通道存在，回归风险不可接受；P2 载荷收缩落地时再把
  `resources/` 从 PROTECT_ONLY 并入 HOST_LOCAL_PATHS（那时 exclude 恰是
  期望语义）。
- 只改 wrapper 不动 legacy——两路径语义分叉（D5 反模式）；legacy 一行
  filter 对称补齐。
- 不做本步、与 P2 分层一起上——§4.3 明文要求保护先行使「旧全量流程 =
  分层超集、回退安全」成立；后置会让中间态的回退不安全。

## Verification

- `tests/test_agent_priv_boundary.py::test_wrapper_protect_only_paths`：
  `PROTECT_ONLY_PATHS == ["resources/"]`、`HOST_LOCAL_PATHS` 不变、
  protect-only 追加环在源文本、无 `--exclude=resources/` 产物；
- `backend/tests/services/test_host_updater.py::test_build_remote_script_
  protects_resources_tree`：legacy 脚本含 `--filter='protect resources/'`、
  mtbf exclude 保留、无 `--exclude='resources/'`；
- matched 面（boundary + host_updater + precheck_sync）61 passed；
- `check:quick` 7 gates 全绿。

## Revisit

- 与 #1929 的 `write-digest` 同类：wrapper 变更随下次 hot-update 升级到
  存量机；未升级机旧保护清单下，控制面源删除仍会传播（低急：控制面极少
  删 resources 文件）。
- P2 分层扩展落地时：`resources/` 从 `PROTECT_ONLY_PATHS` 并入
  `HOST_LOCAL_PATHS`（exclude 届时恰是期望语义）、host-resources 独立通道
  （新受控子命令按 ADR-0037 模式联审）、Ansible 职责归位——另单实施。
