# flash_firmware 1.3.20 参数说明与发版维护

Status: implemented
Class: bug-fix

## Decision

实施 [#3709](https://github.com/DUElost/stability-test-platform/issues/3709)：
补齐 [#3698](https://github.com/DUElost/stability-test-platform/pull/3698)
新增的四项参数说明，修复已完结 B5 的后续维护遗漏，不重开批次。
基线为 `origin/main@318b73d246bdd4c55b09dd9835e3559eee9a42de`。

`backend/schemas/param_docs/flash_firmware.json` 新增 `flash_port`、`gate_pid_scope`、
`require_port_binding`、`require_adb_ready`；四项均从 `1.3.20` 起适用。
沿用 ADR-0060 D2 的登记表与 loader，不改变执行语义。
原首批内容与溯源记录见 [B5-U5 Note](../feature/2026-10-10-b5-u5.md)。

发布源码身份：本基线 `backend/agent/scripts/flash_firmware/` 的两份跟踪文件临时确定性打包，
重建包 SHA256 与 manifest 中 `flash_firmware@1.3.20` 登记值相等：
`893296e676c4d8ce05694ce29b7ff97dbb52772a754e256dbb87c8ec257b5325`。
只在临时目录重建并销毁，没有发布包或运行刷机脚本。

逐项依据（行号属于本基线的 `flash_firmware.py`）：

- `flash_port`：`main:2131–2133/2166–2177`、`_resolve_target_identity:448–460`。
  步骤参数优先于 `STP_FLASH_PORT`；身份检查开启时校验形态，按 serial 查口优先、显式值只是回落。
  两个身份开关均关闭时，门控只反查 serial，不使用显式口。
  形态校验不保证口的实体归属，说明保留错刷邻机风险。
- `gate_pid_scope`：`main:2128–2130/2220–2223/2291–2292/2331–2332`、
  `_gate_other_mtk:592–602`、PID 集合 `357–371`。
  初始门控和重试前门控消费它；门控保持仍固定使用 `_BROM_STAGE_PIDS`。
  `gate_other_mtk` 只控制初始门控调用；重试前与保持阶段不检查该开关。
  关闭它不保证完全停止门控。这是 1.3.20 的发布行为，不在说明维护单中修改实现。
  V6 且 Linux 缺省 `brom`，其它情况 `full`；未知非空值实际走 `full`，不虚构取值校验。
- `require_port_binding`：`main:2117–2122/2239–2244/2341–2347`、
  `_resolve_target_identity:463–466`、`_build_flash_argv:1252–1263`。
  身份无法确定则拒绝；V6 才增加 `-p`。显式开启也不改变 V5 命令形态。
  回显只有在可解析且与目标口不同的情况下触发终止，不声称缺回显也能证明绑定。
- `require_adb_ready`：`main:2117/2123–2127`、`_resolve_target_identity:467–479`。
  缺省独立按 `v6_native` 算，不跟随用户配置后的绑定开关；非 `device` 但口已在可刷态可以放行。
  头部 `:134–135` 的“默认同 require_port_binding”容易被读成跟随配置，本次以执行代码为准。

`test_param_docs_content.py` 增加版本反例与逐项语义条件检查，原覆盖要求保留。
`script-versioning.md` 补充已登记族的新版本同 PR 维护说明、发布源码内容复核与现有测试要求。
只改这四个文件；不改脚本族树、manifest、loader、API、Agent、CI 或其它族。

## Alternatives

- 删除头版本覆盖测试或让旧版本命中新说明：会隐藏维护遗漏或向历史发布单元套用不存在的参数，放弃。
- 原地改脚本或登记新脚本版本：说明在族树外，与执行无关，放弃额外发布成本。
- 将完整后端信息性套件升为 required：超出本需求，门禁范围与成本另由 Owner 裁定。

## Verification

所有本地 pytest 使用本 worktree 的项目 Python 入口与 `scripts/run_pytest.py`，
干净环境不提供数据库 URL，不读取生产 env；conftest 使用独立 testcontainers PostgreSQL。
runner 实测 `memory.max=6442450944`、`memory.swap.max=0`。

- 基线选择器 `test_head_version_documents_every_key_the_script_reads[flash_firmware]`：
  **1 failed / exit 1**，错误恰为四项未登记路径。
- 首版内容与登记表套件：**132 passed / 1 skipped / exit 0**，命令为
  `./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_param_docs_content.py backend/tests/services/test_param_docs_registry.py -q`。
- `./scripts/project_python.sh -m ruff check backend/tests/services/test_param_docs_content.py`：通过。
- 确定性包身份核对：两文件重建 SHA 与 manifest 登记相等，未修改发布单元。
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：**16 gates / exit 0**，含新增 Note 后补验。
- 精确 PR head 的六项 required CI 与完整后端信息性 CI：提交时 pending，
  最终 head 的结果另回填本单 PR 与 issue。
- 熟悉刷机脚本者独立内容复核：首轮绑定 `8ad679f3` 结论为需修改，仅 R1 门控开关前提错误；
  三项其它说明、版本范围、包身份与发版要求已签署。R1 返修待新 head 复核，PR 保持 Draft。
- 合入与线上说明生效：pending，生产未修改。

新增版本用例检查四项在 `1.3.20` 实际选中，在 `1.3.19/1.3.18` 回落键名；
语义用例逐条检查 serial 优先、初始/重试门控与固定保持集合、V5 不带 `-p`、ADB 独立缺省。
这些断言帮助防回退，不能替代独立内容复核。

### R1 返修（2026-10-14）

[首轮复核 R1](https://github.com/DUElost/stability-test-platform/pull/3711#issuecomment-6159329503)
指出新增注意事项将 `gate_other_mtk` 误写为全部门控的启用前提。
本轮仅修正 `gate_pid_scope.cautions`，补充该参数语义断言要求初始/重试/保持阶段的真实条件，
并拒绝旧的全局前提说法，更新本 Note；脚本实现与发版文档不变。

- 增强语义断言对旧文案：**1 failed / exit 1**，命令为
  `scripts/run_pytest.py backend/tests/services/test_param_docs_content.py -k 'preserve_source_conditions and gate_pid_scope' -q --tb=short`。
- 修正文案后，内容/登记表套件加两项 R1 隔离探针：**134 passed / 1 skipped / exit 0**，命令为
  `scripts/run_pytest.py /tmp/stp-3709-r1-implementation/test_probe.py backend/tests/services/test_param_docs_content.py backend/tests/services/test_param_docs_registry.py -q`。
  探针取本 worktree 发布源码，复用 Reviewer 已审计的临时 sysfs/stub 夹具；
  外部 Popen/run 被禁止，ADB/锁/持久化/恢复/重启均 stub，不操作真实 USB。
  两项分别确认关闭开关后初始报告 disabled，但重试仍按 full 隐藏 201c 邻口、
  首次尝试保持仍按固定四 PID 隐藏 2000 邻口。Reviewer 原证据目录未修改。
- 在临时 JSON 副本保留正确事实、分别追加旧前提/关闭后无门控/全部受开关控制三种错误说法：
  **3 failed / exit 1**；不变异控制 **3 passed / exit 0**。
  命令为 `scripts/run_pytest.py -p backend.tests.conftest /tmp/stp-3709-r1-implementation/test_mutation_{red,control}.py -k selected_gate_doc -q --tb=short`。
  测试调用仓库里实际选中说明的语义断言，未修改仓库 JSON；探针、变异夹具和日志均留本轮临时目录。
- 本轮 `check:quick`：**16 gates / exit 0**；Ruff 与 diff --check 通过。
- 首次组合运行同时显式加载及自动发现 backend conftest，重复注册导致收集失败；
  移除多余 `-p` 后取得上述 134/1 最终结果。收集失败不计作负向成功。
- 原 head 完整后端 CI 4074/1 是历史证据，不能代替新 head CI；新 head 结果待回填 PR 与 issue。

## Revisit

后续脚本版本新增/改变参数含义时，同 PR 更新说明及版本适用性。
若目标身份、代际、PID 门控或布尔缺省行为改变，重新核对本文源码依据与语义用例。
说明线上生效由 Owner 另行授权后端控制面部署与只读验收，无 Agent 更新、publish/scan 或 Plan 重指。
