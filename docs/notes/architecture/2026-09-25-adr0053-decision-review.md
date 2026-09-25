# ADR-0053 待裁决项复审

Status: implemented
Class: architecture

## Decision

按 owner「项目开发中、无需线上兼容约束、第一性原理与长期复利」的要求，
基于 `origin/main=e9ca6933` 对证 ADR-0053 与当前代码/测试，形成 v0.2 裁决建议。
2026-09-25 owner 明确确认“同意ADR-0053 v0.2的裁定”，据此将 v0.2 标为 Accepted；
本 Note 的 implemented 仅指裁决记录与文档同步已完成，CAS Phase A–D 均未实施。

已接受文件级内容对象 + 版本化事件清单 + 独立 DLE 观察引用；
baseline 复用必须先解决封口/身份、上下文采集与 scan 输入；
Jira 工作区不共享可写 inode；首版包含并发发布、GC、恢复和真实字节量指标。
修正 v0.1 的全量持续上送、唯一 run 回收路径和统计天然不变三处前提。

开工已刷新远端、Execution Registry 与开放 PR；当时无开放 PR，
本需求以 #3230 显式 declare，沿用独立 worktree；接受后同步 ADR-0025/0028、
scan/upload/merge 契约、存储角色、semantic-ownership 与两份日志链路由文档，均属文档变更。
现有同目录的宽 scope 声明未触及本次具体文件；没有取得其他 Execution 的所有权。

## Alternatives

- 跨 run 逐文件硬链接：可以降低复制量，但身份/并发/GC/消费边界仍需补齐，
  不因保持路径而选择它作开发期终态。
- 整事件 CAS：附件变化会使大转储重复，选择文件 blob + manifest；暂不做分块去重。
- 直接取消 baseline 或缩短 run 保留：丢失观察/事实且不解决根因。
- 新建分布式存储服务：当前没有必要；先复用既有调度与站点文件系统。

## Verification

本轮未连接业务数据库、未读取生产凭据/主机清单、未操作中心盘。
证据范围：当前源码、现有测试、Git/GitHub 只读状态与临时目录实验。

- 现有测试：使用当前 `.venv/bin/python -m pytest`，在独立 worktree、清空继承环境、
  `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0` 下运行
  `backend/agent/tests/test_event_uploader.py backend/agent/tests/test_aee_reconciler.py -q`：
  **63 passed in 3.06s**。首次启动缺 user bus 环境而未运行测试；补齐当前 UID 的
  `XDG_RUNTIME_DIR` 与 `DBUS_SESSION_BUS_ADDRESS` 后完成，不移除内存硬顶。
- 内容身份反例：AST 抽取当前 `EventUploader._dir_sha256`（只运行该纯文件函数），
  在 `TemporaryDirectory` 创建第一棵树 `a=bc`、第二棵树 `ab=c`；
  两棵树 manifest 不同，但原函数摘要相同，输出 `different_manifests_same_legacy_checksum: True`。
  这是序列化缺少边界的可复现问题，不是 SHA-256 算法碰撞；仅记录，未在文档任务中改代码。
- `python scripts/run_gates.py check:quick`：流程通过（16 gates）；隔离 worktree 未配置数据库，
  schema-at-head 按设计 WARN 跳过，不声称验证了业务库 schema。首次检查发现本 Note 缺少
  Status/Class 头，补齐后完整重跑通过。
- 提案轮相对链接、D1–D6 标题与 Proposed v0.2 索引一致性检查、`git diff --check` 已通过。
- 接受轮：Accepted v0.2 与权威指针/里程碑一致性、相对链接、`git diff --check` 通过；
  `check:quick` 完整重跑通过（16 gates；schema-at-head 因未配置数据库 WARN 跳过）。
  仅文档变更，未重复运行提案轮的 63 项既有单测；PR required checks 由 CI 单独核验。

现有测试通过只佐证原有行为；新 CAS、NFS 发布持久性、设备封口协议、外部厂商工具行为、
全链路 baseline 对拍与节省量均 **pending**，不能以本次 63 个既有单测代验。

## Revisit

按已接受的 ADR Phase A→B→C→D 实施；每阶段回填实证与当前实现契约，
不把 Accepted 状态作为 Phase A–D 验收完成的证据。
若文件对象数量、manifest 索引成本或目标文件系统能力测量不满足预算，再裁实现细节；
不撤销观察与内容身份分离、不以静默复制掩盖完整性失败。
N/M 保留参数归 #3230 G4，历史数据删除另有明确授权；本轮不重开生产操作窗口。
