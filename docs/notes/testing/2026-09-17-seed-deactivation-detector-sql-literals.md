# seed 停用判据改按 SQL 字面量：去误报 + 抓漏报（守卫自修，随 #942/#2055 家族）

Status: implemented
Class: testing

## Decision

`tests/test_script_seed_governance.py` 的停用判据是
`"is_active = false" in path.read_text(...)`（**整文件文本匹配**），两个方向都出错，
合起来制造了当前主线的红灯：

- **误报**：`e5f6a7b8c9d0_repair_flash_firmware_seed_identity_2399.py` 的两处
  `is_active = false` **全在注释与模块 docstring 里**（它真正的 INSERT 用的是 VALUES
  里的 `false` 字面量）——它根本没有停用任何既有版本，却被判为「停用但缺引用检查」。
- **漏报**：`i9j0k1l2m3n4_seed_gpu_setup_v104_stable_install.py`（2026-08-31）写的是
  `is_active=false`（**无空格**），精确匹配整份文件都不在扫描面里——一个真实的存量
  违规迁移因此长期不可见。

修法（只动守卫，不动任何迁移）：

1. 判据改按 **SQL 字面量**：`ast` 解析迁移、取字符串常量（**排除**模块/函数/类
   docstring；f-string 取字面量片段拼接），再在**单条字面量内**匹配
   `UPDATE\s+script\b[\s\S]*?is_active\s*=\s*false`（`re.I`）——同一条 SQL 里既有
   UPDATE 语义词又有该赋值才算停用；INSERT 一条 `is_active` 为 false 的**新行**不算。
2. 补两条**判据自身的守卫**：合成文件正/负例（注释与 docstring 的提及不算、INSERT
   不算、真停用必须看见）+ 真实语料落点（误报文件不在集合、漏报文件在集合）。
3. 豁免表补登记 `i9j0k1l2m3n4`：判据收紧把它照出来，而它的日期（2026-08-31）与
   「已在链上」都符合 `_LEGACY_SEEDS_WITHOUT_REF_CHECK` 的口径（2026-09-12 及之前的
   存量、前向守卫），故按存量补登记并在表头注明来龙去脉——**不是**为放行新文件。

语料实测（全仓 39 个含停用语义的迁移）：新判据与旧判据**同为 39 个文件**，差集恰好是
「−1 误报 +1 漏报」；另跑宽判据（`is_active` 与 `false` 同现即命中）复核，未发现
`CAST(false AS boolean)` 之类第三种写法。守卫的表达力是**净增强**，不是放宽。

## Alternatives

- **给 `e5f6a7b8c9d0` 补上引用检查调用**：否决。它没有停用任何版本，检查本身无对象；
  更实质的是，那要求**改写一条已合入且已应用的 revision**（#2258：须附重放迁移），
  且会让「有 plan_step 引用 flash_firmware 1.3.9」的全新安装直接中止部署——用一个
  假前置换一个真风险。
- **把 `e5f6a7b8c9d0` 加进豁免表**：否决。违背该表自述的准入（「新增文件一律不得
  进入本表」）；用豁免掩盖判据的误报，下次同类误报还会以同样方式被掩盖。
- **保持现状**：否决。主线红灯每天污染全量测试的判读（本轮「主线既有红灯」已多次
  出现在其它单的报告里）；而漏报意味着真实的存量违规继续不可见。
- **把判据升级成「执行迁移 SQL」级分析**：不做。静态提取字面量已覆盖当前全部形态，
  执行级分析（如起库跑迁移）与 `tests/` 的「纯离线 + 秒级」准入相悖。

## Verification

- **反例构造（先证伪再采信）**：
  - A 判据退回整文件文本匹配 → `test_deactivation_detector_reads_sql_not_prose` **FAILED**
    且主守卫 `test_new_seed_migrations_deactivating_versions_check_references` **FAILED**
    （复现原红灯）；
  - B 判据退回大小写/空白敏感 → `test_deactivation_detector_corpus_delta` **FAILED**
    （漏报回归；此时主守卫反而变绿——正是「漏报」的形态）；
  - C 抽掉豁免表新条目 → 主守卫 **FAILED**（证明该条目承重，且收紧后的判据确实看见它）。
  恢复后 8 passed。
- 实测命令与结果：
  - `TESTING=1 python -m pytest tests/test_script_seed_governance.py -q` → **8 passed**
    （修前：1 failed / 7 passed，即全仓报告里的那条主线红灯）；
  - `TESTING=1 python -m pytest tests/ -q` → **1373 passed, 0 failed**（红线清零）；
  - `python -m ruff check tests/test_script_seed_governance.py` → All checks passed；
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**。

## Revisit

- **判据仍是「字面量 + 正则」**：若将来出现 `is_active = CAST(false AS boolean)`、或把
  SQL 拆到变量里拼接（`sql = "UPDATE script SET "; sql += "is_active = false"`），仍会
  漏判。今日语料扫描无此形态；真出现时按同一模板收紧（必要时把字面量拼接改成 AST
  常量折叠）。
- **同文件另一条判据仍是文本匹配**：`test_seed_migrations_do_not_delete_script_rows_on_downgrade`
  用 `"DELETE FROM script " in text`——同样会被注释/换行/大小写骗过。本轮不顺手改
  （无现场、且它当前为绿），登记为同类可收紧项。
- **豁免表的终态**：表内 31 条随 #735 的零引用版本退役自然收敛；`i9j0k1l2m3n4`
  的 gpu_setup 1.0.2 若已在计划里零引用，退役后本条即可移除（`test_legacy_allowlist_has_no_stale_entries`
  会提醒）。
