# 源扫描守卫第六批：3 文件 6 处迁移，并把剩余债务拆成「该迁 / 不该照迁」四类（#2639 第六批）

Status: implemented
Class: testing

- 日期：2026-09-20
- 关联：`#2639`（本族立单，已 CLOSED；无单推进）、第四批
  `docs/notes/testing/2026-09-20-source-scan-batch4-playbook-priv-2639.md`、第五批（口径轴二）
  `docs/notes/testing/2026-09-20-source-scan-tmp-path-axis2-2639.md`
- 落点：`tests/test_agentctl_contract.py:1`、`tests/test_install_agent_noninteractive.py:1`、
  `tests/test_site_preflight.py:1`、`tests/test_source_scan_anchor_ratchet.py:1`
- 堆叠关系：本批基于 #2928（第五批）之上开发，须排其后合入

## Decision

**迁移 6 处，全部锚在「替代物」上**（第四批定下的编法）：

| 位点 | 锚点 | 禁词 |
|---|---|---|
| `tests/test_agentctl_contract.py:26` | `install -m 755 "$SCRIPT_DIR/agentctl.sh" "$INSTALL_DIR/agentctl"` | `cat > "$INSTALL_DIR/agentctl"`（内联造脚本＝两份真相） |
| `tests/test_agentctl_contract.py:44` | `HOST_ID=$HOST_ID` | `HOST_ID=auto`（R02-R01：默认 auto 会让多机共用注册身份） |
| `tests/test_install_agent_noninteractive.py:457` | 任务名 `Run install script non-interactively` | `printf '%s\n%s\n'`、`&#124; bash install_agent.sh`（#2324 管道喂 bash） |
| `tests/test_site_preflight.py:266` | `def run_preflight` | `write_text`/`os.makedirs`/`mkdir(`/`open(`/`subprocess`（零写入契约） |
| `tests/test_site_preflight.py:305` | `class Check` | pydantic/yaml/psycopg 三类站点依赖（checks 必须在未装依赖的机器上可 import） |

`BASELINE` **17 文件/37 处 → 14 文件/31 处**；`SITE_FLOOR` 保持 30（现状 31，十位下取仍成立，不必再动）。

**本批真正的产出是剩下的 31 处被拆成四类——其中三类的正确处置是「不照第四批那样迁」**：

- **A 类·不可变已发布脚本版本**（3 文件/7 处：`test_device_flash_scripts.py` 5、
  `test_flash_firmware_v1316.py` 1、`test_flash_preflight_v102.py` 1）。第二批留了个条件：
  「锚点在不可变版本目录 ⇒ 无漂移面」**若被推翻**才值得修。本批实测该前提仍成立
  （`flash_firmware/v1.3.14`、`v1.3.15`、`v1.3.16`、`oobe_skip/v1.1.2`、`flash_preflight/v1.0.2`
  都是磁盘上真实存在的已发布版本），并补上一条第二批没写的更强理由：
  **版本一旦被退役，`read_text()` 直接抛异常——大声失败，不会静默恒真**。
  所以这类位点连「空守」风险都不存在，正式移出迁移队列（占剩余 31 处的 23%）。
- **i 类·文本预处理本身有功能**（1 文件/1 处：`tests/test_ansible_config_channel_2218.py:57`）。
  它判的是**去掉注释与空行之后**的 `configure_agents.yml`。实测 `rsync` 在该文件里出现 1 次、
  且**只出现在注释里**（原文 1 / 去注释 0）——迁到 `SourceGuard` 的原文视图会立刻变红，
  并把「注释里提到 rsync」变成违规。那是**改契约**而不是改写法，故不迁。
  真要修得先给助手加「按视图断言」的能力（`washed` 视图），属工具面改动，另批评估。
  （A 类那两个 `split('"""', 2)[2]` 的位点也带这个形态，已并入 A 类处置。）
- **循环多位点·需要 per-script 锚点表**（1 文件/3 处：`tests/test_deploy_scripts.py`）。
  三处都在 `for script in ENTRY_SCRIPTS / ALL_SCRIPTS` 里，而一条 guard 只能有一个锚点，
  四个脚本各自的「替代物」并不相同：想用来锚 `:125` 的 `deploy_site_identity`
  在 `deploy/preflight.sh` 里**出现 0 次**；四个脚本都有的 `set -euo pipefail` 语义又太弱
  （只证明文件存在）。另批需要先定「per-script 锚点表」的形状。
  顺带记下：`tests/test_deploy_scripts.py:123` 那个 `if literal == "city-b": continue` 让 `"city-b"` 这条禁词**从不生效**，
  是笔误还是有意留档未查清——属另一件事，不在本批动。
- **常规待迁**：9 文件/20 处（31 − 7 − 1 − 3）。这才是第四批那种「逐位点读码 + 编锚」可以继续推的量。

**为什么本批只有 6 处**：先按「形状一致才同批」切，不按处数切。上面三类都是在读码时才发现
「照第四批的编法迁会出事」，与其塞进本批造出假锚点/改契约，不如把它们登记成三类并各给理由——
这与第三批「清单不真就先修清单」、第四批「产物路径不是源扫描」是同一条判断的三个方向。

## Alternatives

- **把 A 类 7 处也迁了凑数**（否决）：无漂移面 + 退役即大声失败，迁移只会增加一次改动与一片假锚点。
- **直接把 A 类/i 类/循环类从 `BASELINE` 删掉**（否决）：它们仍是真·源扫描（i 类尤其如此），
  删了判据就再也看不见；`BASELINE` 是「未走助手的位点」而不是「待办清单」，混为一谈会让下一次
  扫描漏掉整类形态。分类结果写在**本 note 与注释**里，而不是靠删条目表达。
- **给助手加 `washed` 视图顺带解决 i 类**（否决，本批不做）：那是扩能力（新增 API 面），
  与「迁移存量」不同轴；按本族规矩，扩能力要单独一批并自带夹具与变异。
- **`SITE_FLOOR` 下调到 20**（否决）：现状 31，十位下取是 30，还有余量；把兜底闸一路陪着存量下调
  会让它失去「判据被削弱」的报警能力。

## Verification

- 迁移目标三文件：`env -u DATABASE_URL /home/debian13/stability-test-platform/.venv/bin/python -m pytest tests/test_agentctl_contract.py tests/test_install_agent_noninteractive.py tests/test_site_preflight.py -q` → **52 passed**
- 连同棘轮：`… tests/test_source_scan_anchor_ratchet.py` → **62 passed**；
  `test_offenders_equal_baseline_no_growth_no_staleness` 报的 stale 恰为本批 3 个文件、growth 为空
- 剩余存量实测：`scan_offenders()` = **14 文件 / 31 处**，与 `BASELINE` 逐文件注释之和 31 相等
- **变异 6 条，逐条红且红在正确的因上**，7 个被改文件逐字节还原（`restored-identical: True`），
  还原后复跑 **62 passed**：
  1. agentctl 锚点改成不存在的串 → `AnchorDrift`
  2. 往 `install_agent.sh` 塞回 `HOST_ID=auto` → `FormRegression`
  3. 忘记下调 `BASELINE`（把 site_preflight 加回去）→ staleness 分支点名它——**证明迁移是真的**
  4. 把 checks.py 那条退回裸 `assert dependency not in source` → growth 分支点名该文件（测试自身仍绿）
  5. 去掉 `.anchored()` → `GuardMisuse: 守卫写法不合法（空守）`
  6. 往 `install_agent.yml` 的任务名注释里塞 `| bash install_agent.sh` → `FormRegression`
- A 类前提复核（只读）：5 个被钉版本目录全部存在于磁盘；`_SCRIPT_DIR` 解析到
  `flash_preflight/v1.0.2`、`flash_firmware/v1.3.16`
- i 类实测（只读）：`rsync` 在 `configure_agents.yml` 原文命中 1 次、去注释后 0 次
- 门禁（均在本批 rebase 到含 #2928 的 `origin/main` 之后复跑）：`scripts/run_gates.py check:quick` → **12 门通过**；`scripts/run_gates.py check:pr` → **21 门通过**
- 根 `tests/` 全量：`env -u DATABASE_URL <venv>/python -m pytest tests/ -q` → **1691 passed**
- pending：合并后 detached 复跑 + `tools/dev/check_governance_surface.py --check`

## Revisit

- **常规待迁 9 文件/20 处**：按第四批编法继续，建议下一批取 `backend/tests/test_deployment_files.py`(6)
  与 `backend/tests/test_ci_and_test_harness_files.py`(5)——两处都是 5+ 处的大头，且扫的是
  部署配置这类真会搬家的东西；动手前仍要先逐处读码，本批的三类例外都是这么发现的。
- **i 类要不要给助手加「按视图断言」**：先数清有多少位点会用到 `washed` 视图，再决定值不值得扩 API 面。
- **循环多位点的 per-script 锚点表**：定形状时顺带查 `test_deploy_scripts.py:122-125` 的
  `city-b` 死分支（禁词从不生效）。
- **A 类的处置要不要写进判据**（口径轴三：路径根在 `backend/agent/scripts/*/v*/` ⇒ 不算漂移面）：
  本批只是**从迁移队列移出**、仍留在 `BASELINE` 里。若下一批还要为它解释，就升格为判据；
  现在为 7 处新增一条放过规则，收益低于风险（放过方向永远是危险方向）。
- 正向形态 `assert "<字面量>" in 源码` 仍无判据（需单独立单）。
