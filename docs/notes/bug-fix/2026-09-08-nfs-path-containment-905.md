# nfs_path 前缀映射路径逃逸收口（precheck 脚本同步）

Status: implemented
Class: bug-fix

## Decision

#905（R02-F06）：`backend/services/precheck/sync.py` 的 `nfs_path_to_local` 仅做
`_REMOTE_AGENT_PREFIX` 字符串前缀比对，剩余切片直接 `_AGENT_SOURCE_DIR / rel`
拼接——前缀后的 `..` 组件可越界读取、多余 `/` 使切片成为绝对路径并整体丢弃
预期根；root 内 symlink 外指同样可越界。修复为三道独立校验：

1. `PurePosixPath` 拒绝绝对切片（`is_absolute()`）与显式 `..` 组件
   （`".." in rel.parts`）；
2. `resolve()` 后包含性校验（candidate 须为 root 自身或其后代）——拦 root 内
   symlink 外指；
3. 返回形态不变（仍是 `_AGENT_SOURCE_DIR / rel` 拼接串），下游
   `os.path.isfile` + SFTP 上传路径语义等价，现有调用点零改动。

威胁边界（与 issue 口径一致）：触发前提是已有管理员权限登记异常 `nfs_path`
且存在带 SSH 凭据的 Agent 同步——非匿名/普通用户入口；本修复收的是
「控制面进程可读文件经 SFTP 外送」的越界面。

## Alternatives

- 返回 `resolve()` 后路径：放弃——下游语义等价但改变返回值形态，现有测试与
  调用点断言（`str(_AGENT_SOURCE_DIR / rel)`）需联动；校验用 resolve、返回
  保持拼接即可达成同等包含性。
- 在 `nfs_path` 登记入口（scripts.py 创建/更新）另加白名单校验：超出本单
  范围——映射函数是最终防线（纵深），入口校验可作为后续硬化项另立。
- 拒绝 root 本身（rel 为空）：放弃——原行为允许 `nfs_path` 恰为前缀（映射到
  根目录，下游 `isfile` 失败走 failed 分支），保持不变避免行为面扩大。

## Verification

- `python -m pytest backend/tests/services/test_precheck_sync.py`：13 passed
  （存量 8 + 新增 5：父目录穿越 / 中段 `..` / 绝对切片 / symlink 外指 /
  嵌套放行回归）；
- `gov-surface` S1–S12 全绿；ruff 干净；
- Registry：fix-905-nfs-path-containment 全程登记（--issue 905）。

## Revisit

- `nfs_path` 登记入口的格式校验（scripts.py）若后续单独立单，应引用本修复
  的三道校验作为最终防线依据；
- `jira_issue_parser.py` 存量 SyntaxWarning 与本单无关，未触碰。
