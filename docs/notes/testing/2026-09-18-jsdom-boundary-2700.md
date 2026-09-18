# 前端测试的 jsdom 边界制度化（#2700）

Status: implemented
Class: testing

## Decision

把「jsdom 没有布局引擎」从**每个实现方各自重新发现**的隐性知识，变成权威文档里的
显式边界，并指定这类回归的三条承担路径。

- `docs/development/testing.md` §4 新增小节「jsdom 的边界（#2700）」：几何（
  `getBoundingClientRect` 恒全 0）、命中（`elementFromPoint` 无意义）、滚动与
  `position: fixed` 覆盖关系、autofill / 下载 / `document.title` 在 jsdom 下**结构上
  不可测**——写多少用例都是 0 覆盖；
- 承担路径按成本排序：**静态守卫优先**（秒级、离线、无浏览器），其次真实浏览器；
- 附 5 条**已发生**的锚点缺陷表（#2614 / #2453 / #2363 / #2028 / #1708），各注
  「jsdom 为何测不到」——将来若建浏览器层，用例直接取此表，不为覆盖率另造；
- `AGENTS.md`「提交前」加一行指针，让改前端交互/布局的 Execution 在提交前就能撞见边界。

本单**不引入 Playwright**（issue 第 3 条）：浏览器层留待有人承担 nightly 维护成本时再
裁决，挂 nightly 而非 required check。

## Alternatives

- **现在建 Playwright 浏览器层**：被否。flake 隔离与维护成本是长期负担（#169 的启动前提
  同判据），而**当前 0 个测试引用几何 API**——不是「有测试但覆盖不到」，是这条维度上
  根本没有测试，先写文档 + 推广已有范式即可止损；
- **加 lint 规则禁掉测试里的 `getBoundingClientRect`**：被否。当前引用数为 0，规则恒真、
  无红可证，属于「为不存在的违规造守卫」；等出现真实误用再说；
- **什么都不做（维持现状）**：#2614 的实现方不得不在测试注释里自陈边界，说明知识没有
  落点；下一个 Execution 仍会以为「加了 jsdom 用例」就守住了遮挡回归。

## Verification

- `grep -rln "getBoundingClientRect\|elementFromPoint\|offsetParent" frontend/src
  --include="*.test.ts*"` → **0 命中**（文档里「0 个文件引用」这句即此实测）；
- 范式先例实存：`tests/test_frontend_bulk_selection_guard_2614.py`（导入图配对断言 +
  几何字面量单一来源），与 `tests/test_admin_only_read_surface_register.py` 同形；
- 5 条锚点单号逐条 `gh issue view` 核对标题与状态（均 CLOSED，标题与表中缺陷描述一致）；
- `python -m pytest tests/ -q -k "doc or governance"` → 26 passed；
- `python scripts/run_gates.py check:quick` → 12 gates OK。

## Revisit

- 建浏览器层时：从锚点表起步（1280×900 与 1100×900 的坐标级翻页 + `elementFromPoint`
  命中自身、autofill 建用户、title 断言、CSV 落地、改名+改字段并发），挂 nightly；
- 静态守卫出现**结构性假绿**时回头（例：某几何约定无法静态表达，只能靠真实视口）；
- 若将来确有测试开始引用几何 API 并写出恒真断言，再把「禁止在此断言几何」升级为门禁。

关联：[`2026-09-15-fold-alert-guard-and-csv-export-tests-2152-2028.md`](2026-09-15-fold-alert-guard-and-csv-export-tests-2152-2028.md)
（#2028 当时的局部补测，本单把它登记为浏览器层锚点之一）。
