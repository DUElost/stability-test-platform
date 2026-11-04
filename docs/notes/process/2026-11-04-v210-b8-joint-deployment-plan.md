# Start-Log-Scan v2.1.0 与 B8 G2/G3 联合部署编排

Status: proposed
Class: process

## Decision

新增[联合部署计划](../../operations/2026-11-04-v210-b8-g2-g3-joint-deployment-plan.md)，
以生产 manifest 的 eeab11d8、当前 main 的 6b0590a 和工具 tag 指向的 9a1fdab 为取证基线。
计划采用独立版本目录与现行 env 路径入口，在同一窗口内滚动部署；影子开关保持关闭。
v1.1 按用户本轮预检选择“双版本 venv + 生产 pkglist”，取代仅切 `_SCRIPT`：
同时切换 `_PYTHON`/`_SCRIPT`，在新部署副本保留生产 pkglist，并分别验证 Python 3.10/3.13。
上游导出与 release.json 保持原样，部署副本单列配置覆盖与运行环境身份。
包模式检查是此方案的适用条件，工具包登记与迁移作为退出 env 过渡入口的终态。
计划区分工具成员关系文件与 G3 发生映射，记录当前上传接入缺口及 G4/G5、#3875 的激活边界。
没有修改批次规格、ADR、发布物、生产配置或业务事实。

## Alternatives

- 原地覆盖工具目录会丢失可复核身份与回退基线，因此采用新增版本目录。
- 仅切 `_SCRIPT` 会使预检发现的 17 台 Python 3.10 主机无法导入工具依赖，已经不满足联合部署目标。
- 只部署 G2/G3 会留下工具换版 pending；已有单机兼容证据支持继续按双运行环境方案准备，仍保留全机队与两类金丝雀门禁。
- 同窗迁移全部工具到包模式会增加 manifest 与运行环境的交付范围，因此先按当前路径入口制定最小方案；发现实际包引用时停止并补齐发布方案。
- 部署后立即开启影子会跨过全 fleet G3/xlrd、#3875 和完整阶段一前置，因此本次只验收现行兼容。

## Verification

只读取当前 issue/PR、补充裁定、代码、部署 SOP，以及生产 manifest 与指定的非敏感配置键。
实际验证结果：

- `env -u DATABASE_URL -u TEST_DATABASE_URL ./scripts/project_python.sh scripts/run_gates.py check:quick`：
  16 gates 完成，退出码 0；未配置 DATABASE_URL，schema-at-head 明确跳过，不代表生产 schema 已验证。
- 经项目 Python 入口核对两份新增文档的相对链接：3 个均存在。
- `git diff --check`：通过；仅新增计划与本 Agent Note。
- 按 writing-conventions.md §5 核对术语、规则强度、执行者、步骤顺序、警告和状态变更预期。

本次没有运行生产扫描、工具对照、Agent 热更新或数据库迁移。
计划中这些动作均为待执行步骤，不能视为验证通过。
v1.1 的 17 台环境差异、406/5149 行配置差异、同批输入 14/34 个问题及 15.33
逐字节一致结果均来自用户预检反馈，作者没有独立复跑；没有将这些结果扩大为全机队通过。
v1.1 再次运行相同 `check:quick` 命令：16 gates 完成、退出码 0，schema-at-head 仍因未配置数据库而跳过；相对链接 3 个通过。

## Revisit

窗口目标提交、工具 tag、现行依赖或 Agent 包引用改变时，重新冻结物料与回退条件。
G4/G5、成员关系文件接入、#3875 与 ADR-0062 v1.1 正式落地后，另补影子激活放行包。
