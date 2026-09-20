# 源扫描棘轮的「口径轴二」：读 `tmp_path` 下的产物不是源扫描（#2639 第五批）

Status: implemented
Class: testing

- 日期：2026-09-20
- 关联：`#2639`（本族立单，已 CLOSED；无单推进）、
  第四批 `docs/notes/testing/2026-09-20-source-scan-batch4-playbook-priv-2639.md`（本批直接由它的测量结果引出）、
  第三批 `docs/notes/testing/2026-09-20-source-scan-detector-precision-2639.md`（同一手法：默认判红 + 只减不增 + 逐位点钉子）、
  `#2663`（可派生量抄成文字就没人约束它）
- 落点：`tests/test_source_scan_anchor_ratchet.py:1`
- 堆叠关系：本批基于 #2893（第四批）之上开发，必须排在它之后合入

## Decision

**第三批修的是「表达式形状」，本批修的是「路径来源」——两者正交。**
`read_text()` 在 AST 上的形状，对「读 `REPO_ROOT / "install.sh"`」和「读 `tmp_path / "install.log"`」
完全一样，只看形状永远分不开。第四批逐处读码时撞上这件事（`tests/test_site_install.py` 3 处全是
安装产物），当时只能把它**剔除**出迁移批次；本批把那次剔除变成判据本身。

**放过方向是危险的（假阴性静默），所以规则写成三条否决式边界**，每条都有夹具、且每条都由变异验证过会红：

1. **种子只认形参与登记的工厂**：`tmp_path`/`tmp_path_factory` 必须是该函数的**形参**，
   临时目录调用必须在 `_TMP_FACTORIES` 里显式登记。同名局部变量不算（夹具 `tmp_lookalike.py`）——
   否则 `tmp_path = REPO_ROOT / "scripts"` 这一行就能给自己发通行证。
2. **逐位点放过，绝不按函数放过**（夹具 `tmp_and_source.py`：同函数里读仓库源那条必须仍命中）。
   这是本轴最容易自毁的位置，第二批已经为「导入助手即整文件豁免」付过一次学费。
3. **repo-fed 压过 tmp-rooted**（夹具 `copied_from_repo.py`）：`shutil.copy(真源, tmp)` 之后读到的
   仍是仓库内容，判禁词仍是源扫描。搬运动作按**目标路径的实参位序**识别（copy 系第 2 个、
   link/symlink 第 1 个），且**实参够多才作数**——`dict.copy()` 这类零参同名调用不算搬运；
   反向由 `bare_copy_not_a_feeder.py` 钉住：判据也不能因为名字像就整片变红。

**结果**：存量 21 文件/45 处 → **17 文件/37 处**，一次清空 4 个文件（`test_site_install` 3、
`test_site_bootstrap` 2、`test_ssh_security` 1、`test_prepare_env` 1）外加
`test_install_agent_noninteractive` 1 处；`SITE_FLOOR` 40 → 30。放过的 8 处与第四批探针**逐位点一致**
（比对结果写在下面 Verification，不是事后叙述）。

**已知过度判红**：一个函数里只要出现过一次 `shutil.copy(…, tmp…)`，该函数内**所有**临时目录读取
都不再放过——静态分不出「这个文件是被搬进来的还是被测程序生成的」。方向是安全的（多红不漏判），
本批不做补偿，记在 Revisit。

## Alternatives

- **按函数整体放过**（否决）：等于给半个文件发通行证，见边界 2。
- **把这 8 处直接写进一份豁免清单**（否决）：那是把**结论**抄成配置，判据本身不变——下一个同形态的
  新用例照样判红，而清单会随时间失真（#2663 同思路）。规则必须长在判据里。
- **只认形参、不登记工厂**（否决）：语料实测 `gettempdir`/`mkstemp`/`NamedTemporaryFile`/`TemporaryDirectory`
  都真的被调用过；少放过不算错，但会把「轴已生效」的信号稀释成个位数。
- **保留 `mkdtemp` 条目**（否决）：它在语料里只出现在散文/文档串里，没有一次**调用**。
  按 `test_tmp_factories_are_load_bearing` 的既定原则「要么在场，要么删掉」删掉。
  反向地，`_REPO_FEEDERS` 里没出现的 `copyfile`/`link_to` 予以保留：它们只会**增加**判红，
  死条目不是漏判位——放过类与判红类注册表的负担不对称，故只对前者施加「load-bearing」约束。

## Verification

- 判据自身：`env -u DATABASE_URL /home/debian13/stability-test-platform/.venv/bin/python -m pytest tests/test_source_scan_anchor_ratchet.py -q`
  → **10 passed**（新增 3 条测试：`test_axis2_only_subtracts_sites_never_adds`、
  `test_product_path_axis_is_load_bearing`、`test_tmp_factories_are_load_bearing`；判别力夹具 +8 个）
- 真语料放过集与第四批探针**逐位点比对**：legacy 45 处/21 文件 → axis2 37 处/17 文件，
  放过的 8 处恰为 `test_ssh_security.py:207`、`test_install_agent_noninteractive.py:341`、
  `test_prepare_env.py:118`、`test_site_bootstrap.py:207`、`test_site_bootstrap.py:559`、
  `test_site_install.py:368`、`test_site_install.py:863`、`test_site_install.py:889`；
  新增 offender 0、基线失效 0（由 `test_offenders_equal_baseline_no_growth_no_staleness` 双向判定）
- **变异 7 条，逐条红且红在正确的因上**，跑完逐字节还原（`restored-identical: True`），还原后复跑 **10 passed**：
  1. 删掉 repo-fed 保护 → `test_detector_discriminates` 红（`copied_from_repo.py` 被放过）
  2. 种子放宽到同名局部变量 → `test_detector_discriminates` 红（`tmp_lookalike.py` 被放过）
  3. 改成按函数整体放过 → `test_detector_discriminates` 红（`tmp_and_source.py` 丢了仓库源那条）
  4. `BASELINE` 不下调（把 `test_site_install.py` 加回去） → staleness 分支点名它——**证明轴二真的在放过东西**
  5. `_TMP_FACTORIES` 加一条死条目 → `test_tmp_factories_are_load_bearing` 红
  6. `is_product_read` 恒 False（轴空转） → load-bearing + staleness 双红
  7. 放过条件去掉路径判定（全放过） → `test_scan_is_load_bearing` 红（命中数骤降）+ staleness 红
- pending：`scripts/run_gates.py check:quick`、`check:pr`、根 `tests/` 全量、合并后 detached 复跑

## Revisit

- **还有没有「第三类不是源扫描」**：不预设。把剩余 37 处逐处读一遍再说——前两批的教训是
  「清单不真，迁移就在还假债务」，但反过来为凑轴数而造轴同样错。
- **补偿过度判红**：若哪天 site 测试因同函数里一次 `shutil.copy` 而被整片判红、确实困扰到人，
  再按「目标路径与读取路径是否同一表达式根」细化；现在不做，因为方向是安全的。
- 剩余 17 文件/37 处真源扫描迁移，优先 `tests/test_agentctl_contract.py`(2)、`tests/test_deploy_scripts.py`(3)。
- **正向形态 `assert "<字面量>" in 源码` 至今无判据**（锚点漂移必恒真），仍是本族最大未覆盖面，需单独立单。
