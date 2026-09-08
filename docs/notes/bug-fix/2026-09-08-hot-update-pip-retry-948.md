# 热更新 pip 失败重试：deps 安装成功标记（#948）

Status: implemented
Class: bug-fix

## Decision

远程 hot-update 脚本在 `$INSTALL_DIR/.deps_installed_sha` 记录「已成功
pip 的 requirements SHA」。触发 pip 的条件改为：

- `OLD_REQ_SHA != NEW_REQ_SHA`（内容变更），或
- `INSTALLED_REQ_SHA != NEW_REQ_SHA`（首次失败未写标记 / 标记陈旧）

仅 pip 成功后写入标记；失败仍 `exit 1` 且不 `systemctl restart`。标记放在
`agent/` 外，避免 rsync `--delete` 清掉。

涉及：`backend/services/host_updater.py`（`_REMOTE_SCRIPT`）；脚本结构断言于
`backend/tests/services/test_host_updater.py`。

## Alternatives

- 暂存→校验→切换整树：改动面大，超出本单「重试仍装依赖」范围。
- 失败时回滚 requirements.txt：与已 rsync 的代码树不一致，且需备份旧文件。

## Verification

- `test_build_remote_script_retries_pip_when_deps_marker_stale`
- `python -m pytest backend/tests/services/test_host_updater.py -q`

## Revisit

若多 Agent 共装同一 `INSTALL_DIR`（非现行），标记文件需按 venv/实例区分。
