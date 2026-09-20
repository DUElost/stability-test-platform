# 源扫描守卫第四批迁移：playbook 与 agent_priv 共 7 处，并把「读临时目录产物」立为下一根口径轴（#2639 第四批）

Status: implemented
Class: testing

- 日期：2026-09-20
- 关联：`#2639`（本族立单，已 CLOSED；本批继续按无单推进）、
  第二批 `docs/notes/testing/2026-09-18-source-scan-batch2-agent-cleanup-2639.md`、
  第三批口径修正 `docs/notes/testing/2026-09-20-source-scan-detector-precision-2639.md`、
  `#2663`（同思路：数字与现实脱钩比没有数字更危险）
- 落点：`tests/test_update_agent_playbook.py:1`、`tests/test_agent_priv_boundary.py:1`、
  `tests/test_source_scan_anchor_ratchet.py:141`

## Decision

**本批只有 2 文件 7 处，比原计划少一个文件。** 原计划还含 `tests/test_site_install.py`（3 处）。
逐处读码后剔除：那 3 处断言的对象全在 `tmp_path` 下——`tests/test_site_install.py:368` 读渲染出的
`var/www/stability-site/index.html`，`:863`/`:889` 读 `_system_file(tmp_path, "etc/stp/prometheus/…")`。
对**安装产物**判「某词不存在」是行为断言：产物没有真源锚点可编，硬迁移只能造假锚点。
第三批已论证过假锚点比空守更糟——它看起来像一道防线。

**7 处的锚点一律编在「替代物」上，而不是禁词的邻居行**：

| 位点 | 锚点 | 禁词 |
|---|---|---|
| `tests/test_update_agent_playbook.py:31` | 同步任务名 `Sync changed agent code into installed agent directory` | 远端暂存 copy 任务名、`agent_remote_tmp_dir`、整树 `ansible.builtin.copy` |
| `tests/test_update_agent_playbook.py:159` | 替代行 `line: "API_URL={{ agent_upgrade_api_url }}"` | 旧行 `line: "API_URL={{ agent_api_url }}"`（#1250） |
| `tests/test_agent_priv_boundary.py:95` | `def cmd_apply_code(args, conf):` | `--dest`（root 侧 wrapper 收到任意目标 = 任意覆盖） |
| `tests/test_agent_priv_boundary.py:121` | `for item in PROTECT_ONLY_PATHS:` | `"--exclude=resources/"`（#1950 分发断档） |
| `tests/test_agent_priv_boundary.py:177` | `WRAPPER_SRC="$SCRIPT_DIR/stp_agent_priv.py"` | ADR-0037 已收口的 6 条 `NOPASSWD` 宽规则 |

邻居行只能证明「文件还是这个文件」；替代行才能证明「退役是有意的」——被替代的那段一旦搬走，
判据必须报「用例已过期」而不是继续恒真。

**顺带成果：`test_update_agent_syncs_directly_without_remote_staging_copy` 原本三条断言全是负向**，
playbook 里那段同步逻辑整体搬家也照样报绿，该函数此前**零保护**。迁移后它必须先锚定同步任务名，
「扫的还是不是这段实现」第一次成为判据成立的前提。

**棘轮同步下调**：`BASELINE` 由 23 文件/52 处 → **21 文件/45 处**（逐文件注释数字之和实测等于
`scan_offenders()` 的 45），`SITE_FLOOR` 50 → 40。取整到十位而不是贴着现状（43）：
`SITE_FLOOR` 是「判据被削弱」的兜底闸，双向收紧本来就由 `BASELINE` 负责；两个数字都贴现状，
等于每次迁移都要顺手改两处，迟早改漏。

**下一根轴（本批只测量、不实现）**：第三批修的是**表达式形状**（`json.loads`、`yaml.safe_load`
等已登记消费者的产物）；本批暴露出**路径来源**是独立的第二类假阳性——同一个 `read_text()`，
读仓库源文件与读 `tmp_path` 下的产物，判据目前无法区分。用一次性探针（对局部名字做
`tmp_path`/`mkdtemp` 传递闭包）测得剩余 45 处中有 **8 处 / 5 个文件**，且 4 个文件的存量**全部**是这类：

| 文件 | 该形态处数 / 该文件存量 | 位点 |
|---|---|---|
| `tests/test_site_install.py` | 3 / 3 | `:368` `:863` `:889` |
| `tests/test_site_bootstrap.py` | 2 / 2 | `:207`（`(tmp_path/'site.yaml').read_text()`）、`:559`（生成的 fstab） |
| `backend/tests/test_ssh_security.py` | 1 / 1 | `:207`（`known_hosts = tmp_path / "known_hosts"`） |
| `tests/test_prepare_env.py` | 1 / 1 | `:118`（`env_file = tmp_path / ".env"`） |
| `tests/test_install_agent_noninteractive.py` | 1 / 3 | `:341`（假 `chown` 函数的 log） |

即「口径轴二」落地后可在**不写任何守卫**的前提下把存量降到 **17 文件 / 37 处**并一次清空 4 个文件。
这与第三批同理：分母不真，迁移就是在还假债务。**本批同时推翻了前两批的一处判断**：
`docs/notes/testing/2026-09-20-source-scan-detector-precision-2639.md` 的 Revisit（承第二批同口径）
把 `tests/test_site_install.py`(3) 列为「扫的是活动源、属射程正面」的下一批候选——逐处读码后是
`tmp_path` 产物，应从迁移队列整体移出；`backend/tests/test_ssh_security.py`、`tests/test_prepare_env.py`
两处此前无人评估过，探针顺带查出同一形态。

## Alternatives

- **把 `tests/test_site_install.py` 一起迁（否决）**：只能给产物路径编假锚点，正是 #2639 要消灭的形态。
- **把轴二塞进本批（否决）**：轴变更重算的是**所有文件**的分母，必须单独可评审、单独可回滚，
  第三批就是这样落地的；混批会让「迁移 7 处」与「存量口径又变了 8 处」互相掩盖。
- **`SITE_FLOOR = 43`（贴现状，否决）**：兜底闸与棘轮是两个东西，贴现状会逼每次迁移同时改两处。
- **锚点编在禁词所在 task 的前一行（否决）**：只证明文件未搬家，不证明退役是有意的。
- **用 `SourceGuard.of_module`（不适用）**：本批被扫对象是 YAML playbook 与 shell 安装脚本，
  只能按仓库相对路径取文本，故全部用 `of_repo_path`。

## Verification

- 迁移目标文件：`env -u DATABASE_URL /home/debian13/stability-test-platform/.venv/bin/python -m pytest tests/test_update_agent_playbook.py tests/test_agent_priv_boundary.py -q` → **31 passed**
- 连同棘轮三文件：`… -m pytest tests/test_source_scan_anchor_ratchet.py tests/test_update_agent_playbook.py tests/test_agent_priv_boundary.py -q` → **38 passed**
  （`test_offenders_equal_baseline_no_growth_no_staleness` 双向通过 = 基线既没漏下调也没多删）
- **变异判据 5 条，逐条红且红在正确的因上**，跑完 4 个被改文件逐字节还原（`restored-identical … True`）：
  1. 锚点改成不存在的串 → `AnchorDrift: 用例已过期（锚点漂移）`（1 failed / 8 passed）
  2. 往 `update_agent.yml` 的 `vars:` 塞回 `agent_remote_tmp_dir: /tmp/stp-agent-stage` → `FormRegression: 防线回归（真实形态退化）`（1 failed / 8 passed）
  3. 故意保留 `BASELINE` 中已迁移那行 → `这些文件已不含该形态…请把 BASELINE 下调：tests/test_update_agent_playbook.py`（1 failed / 6 deselected）——**这条红证明迁移是真的**：判据确实不再命中该文件
  4. 把 `priv_boundary` 一处退回裸 `assert "--dest" not in text` → growth 分支红
     `新的源扫描型否定断言未走公共锚点助手：tests/test_agent_priv_boundary.py`（1 failed / 6 deselected；测试自身仍绿，红在棘轮——正是想要的分工）
  5. 去掉 `.anchored()` → `GuardMisuse: 守卫写法不合法（空守）…未声明锚点就执行 assert_absent`（1 failed / 21 passed）
- 变异全部还原后复跑三文件：**38 passed**
- pending：`python scripts/run_gates.py check:quick`、`check:pr`、根 `tests/` 全量、合并后 detached 复跑与
  `tools/dev/check_governance_surface.py --check`（结果在 PR 描述与本记录回填处更新）

## Revisit

- **口径轴二（下一批，先于任何迁移）**：让判据识别「读的是 `tmp_path` / `_system_file(tmp_path, …)` 派生产物」。
  安全边界沿用第三批：默认判红、只减不增、并加 `test_tightening_only_subtracts_never_adds` 同款逐位点钉子。
  本批探针已暴露两条必须钉住的实现约束：闭包**必须传递**（`nav = tmp_path/…` 之后 `nav_text = nav.read_text()`
  一跳抓不到）；同一函数「既读仓库源又读产物」时**不得**被 tmp 绑定污染（放过只能按位点、不能按函数）。
- 轴二落地后再迁移剩余 17 文件/37 处的真源扫描，优先 `tests/test_agentctl_contract.py`(2)、`tests/test_deploy_scripts.py`(3)。
- **正向形态 `assert "<字面量>" in 源码` 至今没有判据**（锚点漂移时恒真），仍是本族最大的未覆盖面；
  需要单独立单，不塞进棘轮。
- `BASELINE` 注释里的每文件处数只用于排优先级、不参与判定；轴二落地时要一并重算，
  否则又变成「清单与现实脱钩」（#2663 同思路）。
