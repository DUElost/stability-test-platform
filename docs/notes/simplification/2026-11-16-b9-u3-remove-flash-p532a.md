# 删除独立 BSL 刷机工具 `flash_p532a.py` 与编排 `batch_flash_p532a.yml`（B9 U3）

Status: implemented
Class: simplification

## Decision

按批次 B9 规划方案（#4006 §1 F7、§3 U3，单元 issue #4009）删除两份平台脚本族之外的第二套展锐 BSL 刷机实现：

- `tools/dev/flash_p532a.py`
- `tools/ansible/playbooks/batch_flash_p532a.yml`

两文件仅在 #3980 提交过一次，存在独立主机锁（`/tmp/stp-flash-p532a.lock`）、固定路径与硬编码固件参数，且不会同步 `flash_firmware_unisoc_bsl` 脚本族的安全边界修复；全仓无其他代码、测试或文档引用。

已拷贝到 Agent 主机上的 `/usr/local/bin/flash_p532a.py` 与 `/tmp/stp-flash-p532a.lock` 残留不随本单元清理，由 Owner 在 #4006 §5.3 第 8 步只读盘点后统一处置。

## Alternatives

- **继续保留并在本地脚本同步修复**：否。同一刷机逻辑维护两套实现会持续制造配置与安全边界漂移；平台已由 `flash_firmware_unisoc_bsl` 脚本族承载该能力（#4006 §0、§1 F7）。
- **在本 PR 中同时通过 Ansible 清理主机残留文件**：否。按 #4006 §5.3 第 8 步与 §6 边界，实施者不登录 Agent 主机或变更生产主机文件，残留由 Owner 在激活阶段处置。

## Verification

- `git grep -nE "flash_p532a|batch_flash_p532a"`：除本 Agent Note 外零命中。
- `check_script_packages.py`、`check_tool_manifest.py`、`run_gates.py check:quick` 与 `tests/` pytest 门禁在提交前于 worktree 根执行并记录于 PR 正文。

## Revisit

- 若未来仍需面向单机的离线/紧急刷机工具，应直接调用 `backend/agent/scripts/flash_firmware_unisoc_bsl/` 脚本族并通过 `tool_manifest.json` 受控分发，不得在 `tools/dev/` 或 `tools/ansible/playbooks/` 另建独立刷机实现。
