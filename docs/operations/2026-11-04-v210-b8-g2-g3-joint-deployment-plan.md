# Start-Log-Scan v2.1.0 与 B8 G2/G3 联合部署计划

> 状态：待执行的部署计划 v1.0；本文不代表生产部署授权或验收通过。
> 最后更新：2026-11-04（Asia/Shanghai）。
> 范围：扫描工具 v2.1.0、控制面 G2、Agent G3；B8 当前上传模式保持 CURRENT。

本次建议在同一维护窗口内完成工具换版与 G2/G3 部署，顺序为：备料与离线兼容验证 → 控制面切换（影子关闭）→ 单台 Agent 金丝雀 → 机队滚动更新 → 现行链路验收。影子运行另设激活门禁。本次结束时，`STP_SCAN_ROUND_SHADOW_ENABLED=0`。

## 1. 基线与本次交付边界

以下是编写计划时的快照。执行者必须在窗口开始时重新读取现场，冻结目标提交；后续 main 的变化不自动进入本次部署。

- 生产 `current`：`/home/debian13/stp-releases/eeab11d8-20261103`，manifest 的源码提交为 `eeab11d8aa79a620955ecff3cb6ee0bb3912d9aa`。
- 已拉取的 `origin/main`：`6b0590a7237354e3dcb9a3680eb6ea34107f69f8`。G2 [#3874](https://github.com/DUElost/stability-test-platform/pull/3874)、G3 [#3873](https://github.com/DUElost/stability-test-platform/pull/3873) 已合入。独立复核通过分别绑定 G2 `a9e6fe0`、G3 `cec2ce5`，见 [G2 结论](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6621115596)、[G3 结论](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6624879742)。
- 从上述生产提交到 `6b0590a`，没有新增数据库迁移，没有脚本族或 `tool_manifest.json` 的变更；前端变化仅为依赖文件。G1 的迁移 `b8g1c4a7d2e9` 已有[部署记录](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6601258731)。这不代替窗口内的 schema 对齐检查。
- G0-b 已完成，[最终验收记录](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610027672)已补齐。当前站点配置仍为日志根 `/mnt/stp-log`，控制面根与旧日志根 `/mnt/stp-aee`。
- 工具 [automation-toolkit #14](https://github.com/DUElost/automation-toolkit/pull/14) 已合入。tag `start-log-scan/v2.1.0` 解析到 `9a1fdab479b68519edb7ad1728704a2b54f05893`；tag 对象自身的 SHA 不是源码提交。版本发布不等于控制面或 Agent 已经使用该版本。
- B8 的现行补充规格为 [v1.12 裁定](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6626114382)、[版本前置](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6626130322)和[契约冻结补充](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6626404627)。#3800 正文仍标 v1.9，#3877 正文仍含已被取代的规则；执行者必须一并读取这些补充。
- ADR-0062 v1.1 [#3878](https://github.com/DUElost/stability-test-platform/pull/3878) 在本次前检时为 Draft；[G4 #3877](https://github.com/DUElost/stability-test-platform/issues/3877)及 [#3875](https://github.com/DUElost/stability-test-platform/issues/3875)仍未完成。本计划不把它们记为已交付。

本次交付包括现行扫描兼容、报表校验、发生映射和 G2 代码到位。完整阶段一影子对拍依赖 G4/G5；阶段二代表上传依赖 G6–G8 与 D16 就绪确认，两者均不在本次交付结论中。

## 2. 版本落位与配置方案

当前站点使用工具的 env 路径入口：

```text
STP_BACKEND_DEDUP_SCAN_PYTHON=/home/debian13/Start-Log-Scan/venv/bin/python
STP_BACKEND_DEDUP_SCAN_SCRIPT=/home/debian13/Start-Log-Scan/start_log_scan.py
STP_AGENT_DEDUP_SCAN_PYTHON=/mnt/stp-aee/tools/Start-Log-Scan/venv/bin/python
STP_AGENT_DEDUP_SCAN_SCRIPT=/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py
STP_AGENT_PIP_INDEX_URL=https://pypi.org/simple
```

本次建议采用最小换版方案：从 tag 导出源码，落到新的独立版本目录，只切换两个 `_SCRIPT`，沿用现行解释器并通过 §3 的两侧验证后放行。建议落点如下；执行者必须先核对目录不存在，再冻结实际路径：

```text
控制面：/home/debian13/start-log-scan-releases/2.1.0/start_log_scan.py
Agent： /mnt/stp-aee/tools/Start-Log-Scan-releases/2.1.0/start_log_scan.py
```

发布者不得覆盖旧工具目录或已发布包。发布者必须保留新目录中的 `release.json`，并逐项验证 `files_sha256`、version、tag、git_commit 与 `git_dirty=false`。解释器环境的 Python、xlrd、xlwt、pymysql 版本另外记录；`release.json` 的源码哈希不证明依赖环境一致。

这是沿用现行 env 入口的过渡部署，终态出口是由工具发布单元完成不可变包登记与显式版本引用，再退出 env 回退。该迁移不与本次换版混做。当前 main 的 `Start-Log-Scan` manifest 只有 `2026.09.22`，没有 `2.1.0`；执行者不得直接填写尚未登记的包引用。

> 警告：Agent 的 `STP_DEDUP_SCAN_PACKAGE_REF` 在解析成功时会覆盖上述 env 路径；解析失败又可能回退。仅修改 `_SCRIPT` 不能证明工具换版成功。

执行者必须逐台读取该非敏感键与实际解析结果。若任何一台正在使用包引用，执行者必须停止本方案的工具切换，由发布者先补齐包版本登记及对应发布方案；执行者不得删键强行绕过包模式，也不得把回退旧工具算作通过。控制面的站点源键未配置包引用，不能证明全部 Agent 没有历史残留。

配置约束如下：

- 执行者必须在站点真身 `/home/debian13/stp-releases/env.backend` 修改本次指定键；各 rev 的 `.env.backend` 保持指向 `../env.backend`。
- 执行者必须保持 `STP_SCAN_ROUND_SHADOW_ENABLED=0`，扫描周期 `21600` 秒、等待期 `7200` 秒；等待期必须短于周期。
- 执行者不得改变存储根、retention、PlanStep pin、上传模式或扫描 CLI 参数；尤其不得在本次额外启用 `-inc`。
- 执行者必须保留 `STP_AGENT_PIP_INDEX_URL=https://pypi.org/simple`，不得重新引入此前返回 403 的镜像源。
- 执行者必须在配置变化后的 Agent 热更新使用 `--force`；code digest 相同不能证明 env 已下发。

## 3. 窗口前：备料与放行门禁

执行角色：Owner 选择窗口和确认实际切换范围；部署执行者准备物料并记录证据；复核者检查交付结论。本文没有指定额外实施者或新的方向裁决。

按顺序执行以下步骤：

1. 读取生产 manifest 的 `source.revision`、`readlink -f /home/debian13/stp-releases/current` 和 `/health`，记录窗口基线。
2. 读取最新 #3800 正文与补充、相关开放 PR 和独立复核结论，列出 `BASE..TARGET` 内全部批次。标题带 `[批次 B8]` 也计入；不得只依赖旧 SOP 的 `startswith("批次 B")` 过滤器。
3. 冻结 `TARGET`、工具 tag/commit、两个新工具路径、旧路径、Agent 期望 code digest 和回退 bundle。
4. 若目标新增其他批次、迁移或未满足的 `DO-NOT-SWITCH.txt` 条件，停止本次放行并交 Owner 裁定。
5. 通过主机分页 API 读取全部未退役 Agent 的状态，再用 `GET /api/v1/hosts/{id}` 的 `active_job_count` 选择空闲金丝雀。记录 fleet 分母、离线项、忙碌项与版本分布。不得把历史的 77 台当成本次固定分母；不得用列表接口恒空的 `active_jobs` 判断忙闲。
6. 逐台检查实际工具解析路径、包引用、Python 与依赖；在控制面和 Agent 上核对 `/mnt/stp-log` 挂载及可用空间。挂载探针写入与回读按 G0-b 已批准流程执行。
7. 从干净、位于目标 tag 的工具检出生成发布导出：在工具目录运行 `python release/make_release.py --tag start-log-scan/v2.1.0 --out <新的备料根>`。使用工具文档指定且已核验的解释器。
   预期：生成 `<备料根>/2.1.0/release.json`；源码 commit 为 `9a1fdab479b68519edb7ad1728704a2b54f05893`，逐文件哈希一致。
8. 在隔离临时目录，使用新工具及两侧实际解释器运行 `-h`、`-match_issues` 合成请求、`-dedup_org` 和 `-merge_files_list` 固定夹具。核对 `-merge_files_list` 确实存在；核对匹配入口的成功、非法输入及元数据。临时文件不得放进生产报表目录。
9. 对生产同版本基线和 v2.1.0 使用相同输入、参数、配置、白名单与解析依赖做对照。Run 1193 仅使用既有报表的临时副本做离线对照；历史记录的 11 个问题、33 次发生只有在输入范围相同时才是期望值。记录输入清单与哈希、组成员、问题数、次数、机器数和 xls 差异。
10. 检查新工具生成的 `*_members.json`：schema `stp.scan_membership` 1.0、全部 org 行覆盖、报表哈希及工具 commit 正确。与 G3 对接时核对 `sheet=aeeexp`、`mapping.row == org_row + 1`、Path 原文一致。该项是工具输出验证，不代表文件已送达控制面。
11. 因控制面 MTK/Unisoc merge 共用 `StartLogScanMergeEngine`，对两种既有报表各运行一次离线 merge 兼容验证。B8 的目标范围仍只覆盖 MTK。
12. 按 [control-plane-deploy SOP §0.5 阶段 A](../../.claude/skills/control-plane-deploy/SKILL.md)完成部署源守卫、站点配置下的就绪检查、目标 bundle 与 lock 依赖校验。执行者不得在业务库运行测试；执行者必须通过部署态只读探针检查数据库状态。
13. 保存本次配置键的旧值、旧工具身份、后端回退路径和前端版本。完整配置备份如有必要，必须保留原访问限制，不得进入仓库或部署报告。
14. 提交具体放行包：冻结版本、所有门禁结果、未完成项、切换与回退动作、金丝雀对象和观察窗口。

放行包存在测试失败、实际写入结果未知、依赖安装失败、工具来源不明或无法解释的对照差异时，执行者必须停止切换。Owner 的发布版本同意不代替本次生产部署授权。

## 4. 同窗执行顺序

> 警告：本节包含生产写入、服务重启与 Agent 重启。执行者必须取得 Owner 对本次具体放行包的授权后执行。本文只完成计划编制，没有执行以下动作。

### 4.1 落位与控制面切换

按顺序执行以下步骤：

1. 将已核验的 v2.1.0 导出落到两个全新版本目录。
   预期：两侧 `release.json` 的源码身份和受管文件哈希一致；旧目录保持原样。
2. 将站点的两个 `_SCRIPT` 切到冻结的新路径，并显式写入影子开关 `0`。
   预期：变更仅涉及本次冻结键；Agent 源键使用 Agent 可见的中心存储路径。
3. 按部署 SOP 切换到 `TARGET` bundle 并重启 `stability-backend`。
   预期：服务 `active`，30 秒内 `/health` 成功，schema 精确位于目标 head，版本与预期一致；影子仍关闭。控制面重启也清除旧的 merge 能力探测缓存。
4. 若后端未达到预期，停止并执行 §7 回退。
5. 检查目标的前端源码差异；仅依赖更新且现有前端无本次功能差异时，记录不换包的理由。若放行包包含前端构建，则按 SOP 的独立构建和双 rename 流程换包。
   预期：部署报告写明实际前端入口文件及构建提交，或明确记录本次没有切换前端。

上述固定基线没有脚本版本变化，本次不触发 `/api/v1/scripts/scan` 或脚本发包。执行者不得把“脚本目录登记 scan”与“设备日志 `scan_now`”混用。若窗口目标发生变化，执行者必须重新按 diff 判定。

### 4.2 单台 Agent 金丝雀

按顺序执行以下步骤：

1. 用主机详情确认金丝雀 `ONLINE` 且 `active_job_count=0`，经既有 `POST /api/v1/hosts/{id}/hot-update` 入口更新代码与 env；env 强制下发按部署 SOP 执行。
   预期：响应顶层 `ok=true`；代码包含 G3；实际工具脚本解析到 v2.1.0。
2. 在主机实际 Agent 安装根运行 `venv/bin/python -c 'import xlrd; print(xlrd.__version__)'`。
   预期：退出码 0，输出 `2.0.2`。工具解释器与 Agent venv 是两项检查，不能互相代替。
3. 等待更新后的新心跳，用主机详情检查 `ONLINE/matched`、设备数及 outbox/扫描分片失败计数；必要时用同一份 `artifact_digest.py` 在主机侧重算 code digest。
   预期：心跳晚于更新完成时间；code digest 对应本次 G3；仅 revision 字符串或徽标不作为文件到位证据。
4. 对 Owner 指定的 MTK 金丝雀 PlanRun 运行一轮现行扫描，不带 `fact_round_id` 或 `spill_inputs`。
   预期：org 报表、逐行 `.mapping.json`、清单登记、现行上传以及适用的 merge/extract 完成；文件位于现行命名空间，异串校验没有造成正常报表缺失。
5. 若现行扫描或依赖验证失败，停止机队更新并执行 §7 回退。

### 4.3 机队滚动更新与观察

按顺序执行以下步骤：

1. 从当前发布根按部署 SOP 运行 `batch_hot_update --direct --force`，默认跳过忙碌主机。
   预期：已尝试主机 `fail=0`；逐台结果保留，忙碌与离线项标为 pending。
2. 对每台更新完成的 Agent 重复实际 `xlrd==2.0.2` 导入、工具路径/release 哈希、code digest、新鲜心跳与挂载核验。
   预期：全部检查均可关联到具体 host 和本次更新时间。
3. 若任何一台依赖安装失败、工具回退或报表异常，停止后续放量与新派发，按安全停止处理；执行者不得带着半态宣布完成。
4. 等忙碌主机自然空闲后重跑同一更新命令，补齐所有 pending 项。
   预期：冻结的 fleet 分母全部完成；执行者不得使用 `abort_running_jobs` 或 `--include-active` 强行跨过忙闲门禁。
5. 采集更新后至少两个心跳周期，并观察一轮正常扫描/归档链。
   预期：新报表与映射持续到达；上传失败、分片登记失败、待处理积压和存储水位没有本次引入的回归。

执行者必须根据实际 worker、阶段日志及新产物判断扫描进展；`scan_now` ack 仅代表入队，命令退出成功、旧报表数量或 `matched` 均不等于本轮完成。

## 5. 本次验收与影子激活的分界

本次验收按 [L0–L3 方法](../development/ai/methods/batch-activation-acceptance.md)记录，尚未实际运行的项目一律标为 pending：

- **L0 身份与配置**：控制面/Agent 的代码身份、工具 `release.json` 与源码哈希、实际解释器依赖、站点键、日志根和影子关闭状态。
- **L1 运行事实**：当前主机分母全量更新；心跳新鲜；现行报表和 G3 映射落盘、分片登记成功；上传/merge/extract 与本轮输入对应。纯 MTK run 的报表集合没有混入其他设备或错误的 Unisoc 报表。
- **L2 金丝雀行为**：一次已授权的现行 MTK PlanRun；至少包含一个已有本地事件的前置状态。按同一台机器、同一次错误核对 DLE、xls 行和映射，保留原始证据；不得跨机器或发生拼接证据。
- **L3 反例证据**：引用当前实现提交的 CI 与独立复核记录，覆盖异串拒绝、非法轮次不降级、空回执/缺失/迟到区别、查询故障降级、溢出与本地同身份、关闭开关兼容。生产环境不注入这些破坏性反例。

`*_members.json` 是工具的“行 → 工具分组”文件；`*.mapping.json` 是 G3 的“行 → DLE/发生身份”文件。当前 G3 的 `upload_scan_report` 只复制报表并生成发生映射，没有上传或登记工具成员关系文件；staging 随后回收。执行者不得因工具已产出 members 文件就宣布 G4 输入端到端就绪。

G4 接入单元必须补齐成员关系文件的上传、登记及与报表哈希的绑定，并按冻结契约验证读取；缺失或不可用按降级覆盖处理，平台不得自行比较签名。该项交付之前，本次只能证明工具本地输出和现行链兼容，不能证明完整问题归并与代表选择。

**影子激活是后续里程碑。** 部署执行者必须在开启前核对以下全部条件：

1. Owner 已批准本次影子激活范围，G1–G5 相应交付与必须复核结论齐备。
2. 全部 Agent `ONLINE/matched`、实际包含 G3，且逐台可导入 xlrd；任一台不满足均不得打开开关，见[人工闸门](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6621527019)。
3. 长期开启前，#3875 的 settled 出口与“下发前识别 Agent 轮次能力”代码防护已交付；人工一次性检查不代替该终态出口。
4. ADR-0062 v1.1 的正式落点、G4/G5 和 members 端到端接入已经核对，工具调用只使用冻结版本。

当前开关是控制面全局开关，没有单 host/单 run 的 allowlist。执行者不得把全局开启描述成只对某个金丝雀生效；受控演练的范围与窗口必须另行写明。首次开启只观察 RUNNING 的 run，历史终态 Run 1193 不会被 G2 自动追溯建轮，因此它只能用于离线对照。

后续影子验收还包括周期轮、最后轮、补充轮、恢复重试、迟到隔离、溢出临时占用和空 key/未映射/降级覆盖比例。完整对拍按 ADR-0062 §9 检查问题数、发生映射、次数与机器数、代表齐全率、应上传字节和交付范围；G2/G3 部署不能代替这项验收。

## 6. 安全停止条件

以下各项触发后，执行者必须停止后续放量或激活，保留证据并交 Owner 裁定续行：

- 任一 Agent 缺 xlrd、安装失败、工具回退、版本未知，或更新后不再正常上送现行报表。
- 任一日志根挂载/探针失败；15.4 可用空间触发健康页水位；写入结果未知或疑似丢失证据。
- 新旧工具对照出现无法解释的差异，或 `-merge_files_list`、成员关系契约验证失败。
- 影子文件进入现行清单、现行上传集合、merge 或 extract；出现错误轮次或跨 run/host 的输入。
- 控制面健康或 schema 检查失败，或目标包含未放行的迁移/批次。

级别无法确定时，执行者必须按安全停止处理。应上传字节与模拟值的差异分类沿用 #3800 §5，不把历史模拟值当成本次生产测量结果。

## 7. 回退顺序与保留证据

> 警告：停用影子开关不能撤销已下发的 Agent 扫描，也不能自动回退 Agent。执行者不得删除轮次事实、映射、报表或溢出台账来恢复表面一致。

按顺序执行以下步骤：

1. 若影子曾开启，恢复 `STP_SCAN_ROUND_SHADOW_ENABLED=0` 并按 SOP 重启控制面。
   预期：停止新影子调度；对已下发工作逐项记录排空或中断结果，迟到产物保持独立命名空间。
2. 将控制面与 Agent 源键的 `_SCRIPT` 恢复为 §2 记录的旧路径，并重启后端加载配置。
   预期：控制面解析旧工具；旧源码、解释器与回退所需配置仍在位。
3. 若需要回退代码，先比较目标与旧 bundle 的迁移集合，再按 SOP 恢复旧 `current`。
   预期：固定的 `eeab11d8..6b0590a` 没有新增迁移时可使用已准备的旧 bundle；若窗口目标另含新迁移，执行者必须停止该回退动作，由 Owner 裁定兼容方案，不能只重指 symlink。
4. 从实际选定的回退发布根对已更新 Agent 滚动执行 env 强制下发；需要回退 G3 时同时下发旧代码。
   预期：每台主机的旧工具/代码身份、新鲜心跳和现行扫描行为重新验证；忙碌/离线项仍按 pending 跟踪。
5. 若本次换过前端，按 SOP 的双 rename 流程恢复前端。
   预期：线上入口与保存的旧入口一致。
6. 复跑健康、工具身份、主机分母和一轮现行链路核验。
   预期：回退结果有新证据，未完成项明确列出；新版本目录与本次证据保留供定位。

本次不实施数据库 downgrade、旧日志迁移/删除、PRUNED 下载修复或 Agent 本地删除策略。

## 8. 部署报告与当前未完成项

部署执行者必须报告：

- 时间窗、BASE/TARGET、bundle 路径、工具 tag/commit/release 哈希及实际解析路径。
- 配置键变更摘要、schema 结论、前端换包或不换包的结果。
- 金丝雀对象与本轮产物；每台 Agent 的更新、xlrd 导入、工具身份、心跳和挂载结果；汇总必须有明确分母。
- `batch_hot_update` 的 SUMMARY、失败/忙碌/离线项、补齐结果和实际观察窗口。
- L0–L3 的证据链接、停止/回退情况、所有 pending 项。
- 分别写出“代码部署”“工具换版”“G2/G3 现行兼容验收”“影子激活”“完整 B8 阶段一对拍”的状态。

**本稿编写结束时**：已核对远端 issue/PR/补充裁定、main 的代码与测试入口、生产 manifest 及指定非敏感配置键。尚未构建本次 bundle 或工具导出，尚未逐台检查 Agent，尚未运行 v2.1.0 对照、金丝雀或任何生产切换；这些执行验收全部为 pending。没有因 G2/G3 issue 已关闭而推定其已部署。
