# `protect resources/` 只护目录节点：改为整树 `resources/***`（#2019）

Status: implemented
Class: bug-fix

## Decision

`PROTECT_ONLY_PATHS`（apply-code 的 rsync filter 表）由 `["resources/"]` 改为
`["resources/***"]`，并同步修正两处「声称已整树保护」的注释。

**rsync 语义**（本轮三组实测，传输根 = agent 目录）：

| filter | 源树含 `resources/*` 时接收端 `resources/**` 的其余内容 |
|---|---|
| `protect resources/` | **全部被删除**（b.bin / sub/c.bin / mtbf/apk.bin 皆删，rsync 退出 0） |
| `protect resources/***` | 全部存活，且源树新增项正常同步 |
| 不带 protect | 同样全删（说明 protect 一直没在「树」这一层生效） |

尾斜杠模式在 rsync 里只匹配**目录节点本身**；`dir/***` 才是「该目录及其全部内容」。
影响面为零（P2 分层后 code 载荷本就不含 `resources/`），但声明与实现不符——后续维护者
按注释相信「大件已受保护」而放松其它防护时才会显形（例如分层回退、或载荷默认值被误用），
且损失是静默的（rsync 退出 0）。

### `mtbf/` 的同型核对（issue 要求）

`HOST_LOCAL_PATHS = ["resources/mtbf/"]`（exclude **+** protect）**保持原样即可**，
本轮实测：

- 只 `--exclude`（无 protect）→ 接收端 `mtbf/apk.bin` **被 `--delete-excluded` 删除**；
- `--exclude` + `protect resources/mtbf/`（当前写法）→ **存活** ✓；
- `--exclude` + `protect resources/mtbf/***` → 同样存活 ✓。

差别在于 `mtbf/` **同时被 exclude**：rsync 不会进入一个受保护的目录去清空它，故节点级
protect 足够；而 `resources/` 参与传输、会被递归比对，节点级 protect 挡不住子项删除。
两条规则的差异已写进 `HOST_LOCAL_PATHS` 上方的注释（不改值——改动它没有实测收益，
反而会让「exclude 是必需的」这个前提变得不显眼）。

### 同步修正的文档/断言

- `backend/services/host_updater.py` 的注释：补上「树的写法是 `resources/***`」，
  并指明 filter 参数由 wrapper 的 `build_apply_code_filters()` 生成（#2180 后脚本侧零 filter）；
- `tests/test_agent_priv_boundary.py::test_wrapper_protect_only_paths`（既有断言
  `PROTECT_ONLY_PATHS == ["resources/"]`）→ 改为整树形态；
- 两处历史 Note 补交叉链接：`2026-09-14-protect-resources-p2prep-1950.md`（原文写的是旧写法）、
  `2026-09-15-deploy-payload-exclude-alignment-2030.md`（原文标注「#2019 是独立缺陷，未在本单修」）。

## Alternatives

- **保留 `resources/` 并额外加 `--exclude=resources/`**：会停止分发 `resources/`（P2 独立通道尚未
  铺开时等于分发断档），与 #1950 的裁决冲突，否决。
- **依赖「源树本就不含 resources/」这一现状**：正是本单要消除的隐式前提——声明与实现不符时，
  任何一次载荷形态变化都会静默删数据。
- **把 `mtbf/` 也改成 `***`**：无实测收益（exclude 在场时两种写法等效），却会让注释里
  「exclude 必需」的原因更难看清；不改，只把语义写清楚。

## Verification

worktree `.wt/stp-2019-protect-tree`（base `38c76ed0`）：

```bash
python -m pytest tests/test_agent_priv_apply_code_protection.py \
                 tests/test_agent_priv_boundary.py \
                 tests/test_remote_script_privilege_paths.py -q   # 25 passed, 7 skipped
python scripts/run_gates.py check:quick   # [OK] 10 gates
python scripts/run_gates.py check:pr      # [OK] 19 gates
```

- 新增**真实 rsync** 用例 `test_resources_tree_survives_apply_code_rsync_when_source_has_resources`：
  源树含 `resources/new.bin`、接收端含 `resources/mtbf/apk.bin` + `resources/aimonkey/am.bin`
  + 一份陈旧文件 `(非 resources)/stale.py`；断言前两者与新项存活、`stale.py` 仍被删
  （证明 `--delete` 未被削弱）；
- **反例构造**：还原 `stp_agent_priv.py` 到 HEAD → 该用例与 `test_apply_code_filters_protect_deploy_metadata`
  双双失败（`resources/mtbf/apk.bin` 被清）；恢复后全绿；
- 7 个 skip 是本 worktree 缺 `DATABASE_URL`（需控制面模块导入的用例）与 docker 环境所致，
  非本单引入。

## Revisit

- 若 P2 资源通道铺开后 `resources/` 从 code 载荷彻底消失，`PROTECT_ONLY_PATHS` 可退化为
  纯注释性的说明（那时「保护」不再有实际删除面可挡）——但**不要**在没有实测的情况下删除，
  载荷回退是已知的可能路径。
- 同类写法复核：仓库内其余 `--filter=protect` 使用点（本轮 `grep` 只命中此处与历史 Note），
  若将来新增 protect 规则，一律写 `dir/***` 形态；`protect <文件>` 不受此影响（文件节点即全量）。
