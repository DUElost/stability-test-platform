# B8 G4：影子轮读取成员关系、发生入库与代表选择

Status: proposed
Class: feature

## Decision

实施范围为 #3877 正文 v2.1，工具格式取规划者评论 6704532993 指定的
`automation-toolkit` tag `start-log-scan/v2.1.1`（commit `dc5d9c1fe22273dcfc28e41b285e8fdb3bfb6694`）。
按 #3800 评论 6704562784，开工判定只统计 MTK：Run 1231/.13 的 37/37 组与
Run 1199 的 71/71 组均为相同非空 occurrence_key，合计 108/108。
原 Run 1141 属 UNISOC；混合平台的 10.9% 不再作为 MTK 数据。规划者更正的
MTK 多余 DLE 占比为 4.1%，UNISOC 为 58.9%。

`scan_membership.py` 集中读取 xls、G3 映射和成员关系文件，并调用发布工具的
`-match_issues`；平台没有签名相似度比较或分支推断。
影子上送只新增成员关系文件及分片引用。现行上传不读取或上送该文件；
MTK 映射增加证据、重复 DLE 和 unmapped 原因，不改变 #3720 拒绝判定。
证据按一个事件目录内的非空 dbg、mobilelog 与已完成 bugreport zip 判定，
支持现有 correlated 目录布局，跳过符号链接，不查兄弟事件目录。

入库与选择只经 G1 服务层。轮次保存 merge 状态、摘要、时间，新增一次迁移。
摘要保存候选的单事件证据快照，供后续最后一轮选择先前待选的发生。
失败回滚本次事实和选择，记录 FAILED；检查周期重入队。OK/NO_INPUT 重跑不变。
G4 用事务级 advisory lock 串行化 MTK 写入，再按 PlanRun → scan_round 加锁，
避免不同 run 同时写同一全局发生身份。次数与机器数仍由 G1 查询；
发生在另一个 run 再次被观察时，通过 DLE 关联也计入该 run，保留首见事实。

## Alternatives

- 平台复刻 CausedBy 或 singleton 判断：违背正文与 ADR-0062，未采用。
- 缺失成员关系时自行比较：未采用，整份报表计降级。
- 按 Path 或 DLE ID 推断 occurrence_key：未采用。空 key 按已关联 DLE 复用发生；
  新问题的 fail-open 代表身份使用正文规定的 unprovable + dle_id。
- 直接累加报表 Count/DeviceCount：未采用，避免重扫和溢出重复计数。

## Verification

以下均在本机隔离 worktree 执行。pytest 经 `scripts/run_pytest.py`，cgroup 上限
6 GiB、swap=0；控制面测试使用 testcontainers，不读取生产凭据。

- main 基线 `3e605a1` 上先新增四种 sidecar 降级、匹配顺序、fail-open 跨轮、
  已选不改选，共 7 项反例。实施前全部因缺少 G4 入口失败；首次实施后 7 项通过。
- `env -u TEST_DATABASE_URL ./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_shadow_merge.py backend/tests/services/test_shadow_scan_rounds.py backend/tests/test_issue_fact_store.py tests/test_b8_g4_contract.py -q --tb=short`：增加 SAQ 现行链与版本/分组回归后 83 项通过，最终结果见 PR。
- `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python scripts/run_pytest.py backend/agent/tests/ -q --tb=short`：3086 passed，1 failed。
  唯一失败为 `test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails`；
  在独立 main@3e605a1 worktree 用同一 clean env 单独复跑，也失败。
  未修改该发布脚本或无关测试。
- 隔离空 Postgres 16 容器实跑 Alembic upgrade head → downgrade -1 → upgrade head：成功；
  新增三列存在 → 全部撤销 → 恢复，revision 为 b8g4a719c203。
  初次直接使用 `-c backend/alembic.ini heads` 的命令因 script_location 相对路径错误失败；
  随后通过 Config 显式设 backend/alembic 执行上述隔离试验。
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：16 gates 通过。
  首次因新增函数内导入失败，移到模块顶层后通过，未放宽基线。
- main 与当前分支运行同一个离线 UploadManager 夹具，固定时钟和输入，现行报表、
  映射、分片三份文件 SHA-256 逐字节相同；两边均未上送现行 sidecar。
- Run 1193：只复制 `/mnt/stp-log/dedup/1193/mtk/*_org.xls` 的 12 份文件到
  `/tmp/b8-g4-run1193-20261107`，用发布 v2.1.1 执行 `-dedup_org`，12/12 成功生成 sidecar。
  G3 空来源回放与读取层 12/12 通过格式/行号/Path/sha256 对接（不宣称身份映射成功）。
  `-match_issues` 对首批 6 份给出 11 个问题、33 个未过滤成员行；全部 12 份给出
  14 个问题、143 个未过滤成员行。不同输入集合不可混称同一验收基线。
  原目录无伴随映射。完整 G3 → G4 入库/次数/机器数对照尚需离线 DLE 身份快照，
  未从报表 Path 推断身份，未连生产库，未对终态 run 触发扫描，未写原目录。

CI 首轮发现控制面测试引用根 tests 的不稳定导入，以及新增 xlrd 读取点未登记；
夹具已移到 backend.tests.fixtures，并按 #739 守卫更新依赖使用面与规划，未放宽守卫。
单独收集/运行 G4 控制面测试 12 项通过。

## Revisit

本 PR 保持 draft；完整 Run 1193 入库对照与 required CI 未完成前不交付复核结论。
Agent 部署与 #3889 的修复合并在同一次 Agent 发布，本单不部署、不激活。
#3875 的并行 PR #3896 修改相邻服务与调度代码并新增迁移；两者串行集成时，
后合入者需要对齐迁移父版本并复跑迁移/受影响测试，不创建额外 merge revision。
若新组代表未映射却有可映射成员，当前合并明确失败而不猜测问题身份；
出现此类真实输入时应交规划者处理代表身份规则缺口。
