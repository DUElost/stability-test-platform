# #739 面③：墓碑清理（tools/archive + ci_check_migrations）与依赖收敛规划落点

Status: implemented
Class: bug-fix

## Decision

1. **删除两处墓碑**：`tools/ci_check_migrations.py`（379B 壳，自述「勿再运行」）与
   `tools/archive/` 目录（含它指向的旧实现 `tools/archive/ci_check_migrations.py`，3858B）。
   删除前的全仓引用扫描（`backend/ tools/ scripts/ tests/ deploy/ .github/ frontend/src`，
   含 `*.py/*.yml/*.yaml/*.sh/*.toml/*.cfg/*.ini`）只剩：两者**互相引用** + `tests/test_removed_env_keys.py`
   一处历史注释（已同步措辞）。历史实现改由 git history 取回，不再以「墓碑壳 + 存档副本」
   双份形式留在工作树。
2. **依赖收敛规划落点**（#739 面③第二项）：`docs/notes/process/2026-09-20-dependency-convergence-739.md`
   （`Status: proposed`，阶段 0 已实现、1–3 待触发）。核心结论：**xlwt 的替换不是换库，而是
   产物格式迁移**——`openpyxl` 不能读写 `.xls`，而 `.xls` 是 Toolkit（`python3-xlwt` 侧）、
   JIRA 上传链（`--add-main-excel`）、归档/下载面的既有契约；给出使用面、消费方清单、
   三阶段路径、触发条件与出口判据。`apscheduler` 维持 `>=4.0.0a6,<5.0`，记跟踪判据与升级/回落动作。
3. **阶段 0 门禁**：`tests/test_excel_dependency_inventory.py` 冻结 `xlrd`/`xlwt` 的**生产**
   使用面（`dedup_extract.py: xlrd`；`dedup_scan.py: xlrd + xlwt`）——新增文件/新增库即红，
   台账腐烂（已移除仍在册）亦红；并断言规划文档存在且含关键内容（防「台账在、规划丢」）。
4. 台账指针写进 `docs/development/dependencies-and-quality.md`（依赖清单的权威入口）。

## Alternatives

- **保留 `tools/ci_check_migrations.py` 壳、只删 archive 实现**：弃——壳的唯一信息是「去别处看」，
  而「别处」正是要删的存档副本；git history 是更权威的存档。
- **本单直接实施 xlwt→openpyxl**：弃——`openpyxl` 不能读写 `.xls`，等于一次性打断 Toolkit /
  JIRA 上传链 / 归档下载三个消费方；需要跨方窗口（规划文档 Alternatives 已论证）。
- **只写规划、不加门禁**：弃——规划的价值依赖「使用面变化时被发现」；无门禁时新增 `.xls`
  耦合会静默扩散，规划退化成一次性文档。
- **为 apscheduler 加机器告警**：弃——pin + lock 已锁定；跟踪对象是上游发布节奏，
  机器判据需外部状态，引入不必要耦合。

## Verification

- 删除前引用扫描：仅互引 + 1 处历史注释（见 Decision 1）；
- `pytest tests/test_excel_dependency_inventory.py tests/test_removed_env_keys.py -q` → **8 passed**；
- **反向验证（门禁有牙）**：新建 `backend/services/_mutation_probe_739.py` 写 `import xlwt`
  → `test_production_excel_usage_matches_inventory` FAILED；删除探针后复跑 → green；
- `python scripts/run_gates.py check:quick` → **[OK] 12 gates**；`check:pr` → **[OK] 21 gates**
  （含 gov-surface S10 四节/Status 契约——首轮因 `Status: planned` 不在允许集报红，改
  `Status: proposed` 后通过）。

## Revisit

- **阶段 2/3 启动时**：同步 `_INVENTORY` 与规划文档（触发条件写死在规划里）；
- `tests/test_removed_env_keys.py` 的撞名教训以「历史曾同名」措辞保留——判据仍取具体文件；
- **后续墓碑清理沿用本单核查清单**：全仓引用扫描（含 CI/sh/toml）→ 确认无活引用 →
  删除 + 同步引用注释 + 在 Note 里写明 git 取回方式。
