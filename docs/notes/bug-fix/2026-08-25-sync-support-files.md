# sync 治愈路径补推支撑文件（#404 冒烟副产品修复）

Status: implemented
Class: bug-fix

## Decision

`push_mismatched_scripts`（precheck 轻量 sync）此前只推入口文件 + 硬编码的
`_adb.py` 特例，而 agent 侧 `verify_scripts` 按 `support_files_manifest`
逐个比对支撑文件——host 缺/旧 `_lib.py` 时：verify 正确报 mismatch →
push"成功" → reverify 原样再挂 → fatal `script_verify_failed`，治愈路径
对该类漂移永久失效（#404 冒烟 Run #222 实证；当时以整机 hot-update 解锁）。

修复：push 循环按 manifest 键遍历支撑文件逐个推送（本地缺文件如实计入
`failed` → partial_fail，不静默）；CR 清理 + chmod 的 exec_command 改为
按键**实际推送成功**的远端路径列表构建（顺带修正旧行为「对全部 mismatched
条目执行清理，含 put 失败者」的不精确）。`_adb.py` 特例保留（先于 manifest
机制存在，幂等）。

## 放弃的备选

- **一律走整机 hot-update**：拒绝作为治愈主路径——hot-update 含服务重启与
  全树 rsync，per-run 治愈用它过重；保留其 fallback 地位不变。
- **按 verify ack 的 exists 标志选择性推送**：拒绝——put 幂等且廉价，
  选择性逻辑徒增分支；全量推 manifest 即可。
- **把 `_adb.py` 并入各脚本 manifest 后删除特例**：方向正确但需迁移既有
  Script 行的 manifest 数据，超出本缺口修复半径；特例无害，留待后续清理。

## 如何验证

- `test_precheck_sync.py` 新增 3 例（假 SSH/SFTP 传输层）：
  ① manifest 支撑文件随入口一起推送、远端路径正确、CR/chmod 清理覆盖之；
  ② 本地缺支撑文件 → `partial_fail: pushed=1, failed=…support file not found`
  （入口已推但整体失败，reverify 必挂的语义如实上抛）；③ 无 manifest 条目
  行为不变（仅入口 + `_adb.py`），回归锁定。
- 关联套件（admission step4 / plan_precheck / dispatch_retry / step5a）
  87 例全绿；backend 全量 **1633 passed**；ruff 干净。
- 部署生效后，#222 场景（host 缺 `_lib.py`）应自愈：verify 报 mismatch →
  sync 推齐 → reverify 通过准入。

## 边界与何时重议

- 生产激活需随下次部署窗口（本修复仅改控制面代码，重启即生效，无迁移）；
- 若未来 manifest 出现目录型键或超大支撑文件（当前均为单文件 `_lib.py`），
  再评估分批推送与并发上限。
