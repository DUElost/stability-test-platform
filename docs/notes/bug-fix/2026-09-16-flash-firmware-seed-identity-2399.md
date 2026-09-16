# flash_firmware seed 身份错位：守卫版本 off-by-one 修复 + 空库自举三面同源门禁（#2399）

Status: implemented
Class: bug-fix

## Decision

**不动历史 revision（#2258），在链尾加一条收敛迁移，并把「为什么会错」变成机器判据。**

缺陷：两条 seed 迁移把「守卫版本 / 写入版本」比本文件真正 seed 的版本小一整个版本——
`t2u3v4w5x6y7`（文件名 v138）守卫并写入 `1.3.7`，`u3v4w5x6y7z8`（文件名 v139）守卫并写入
`1.3.8`，但两者的 `_CONTENT_SHA256` 与 `nfs_path` 都是**各自文件名那个版本**的真值。
从 v1.3.10 起模板已改对。后果按库分两条岔路，这也是它长期潜伏的原因：

- **老库（含生产）**：守卫行已存在 → 走 UPDATE 分支，只覆盖参数与 `is_active`，从不碰
  sha/nfs → 注册表看着是对的；
- **空库自举**（新部署 / dev / CI `pr-migrate-empty-db`）：插出「version=1.3.8、
  内容=1.3.9」的幽灵行，而 v1.3.9 **根本没有行**。

`script_catalog` 以 `content_sha256` 判 conflict 且**只跳过不修**（要管理员显式
`force_rebaseline`），所以新环境不会自愈；而那行的 `nfs_path` 指向 `/v1.3.9/` 却自称
1.3.8，在挂了 `/opt` 的路径布局下有**静默执行错误版本**的可能——直接破坏「已发布版本
不可变」的可追溯性（同族三次：#751 / #1276 / #2399）。

落法三件：

1. **`backend/alembic/versions/e5f6a7b8c9d0_repair_flash_firmware_seed_identity_2399.py`**
   （head 从 `d4e5f6a7b8c9` 前移一条）：全部 `WHERE` 命中才写的幂等自愈，先例是 #1717 的
   `f6a5b4c3d2e1` 与 #2322 的 `d4e5f6a7b8c9`。
   - 1.3.8 行**只在带幽灵签名时**（sha == v1.3.9 的，或 path 落在 `/flash_firmware/v1.3.9/`）
     才收敛到 v1.3.8 真值 → 生产上零行命中，no-op；
   - v1.3.9 缺行则补，常量逐字抄自 `u3v4w5x6y7z8`；
   - **补行 `is_active = false`**：链上 `j0k1l2m3n4o5`（seed 1.3.10）的停用列表按错位世界写到
     1.3.8 为止，补成 active 会给新环境凭空多出一个可派发版本。`false` 与「该行今天不存在」
     对派发面等价，要不要启用交给 scan 或人工。
   - **不碰任何版本的 `default_params` / `param_schema` / `is_active`**（#942 纪律）。
     1.3.8 行现有参数来自 v1.3.9 的 seed，而 v1.3.7/1.3.8/1.3.9 三版 seed 参数字节相同，
     故无残留错配——这个等价是**偶然的**，已被判据 3 钉住，不许再靠运气。
2. **`tests/test_seed_revision_version_guard.py`（静态、离线、PR 路径）**：对全部 18 条带
   `_CONTENT_SHA256` 的 seed 迁移断言「文件名版本 == 写入行 version」，且 `_CONTENT_SHA256`
   必须等于**磁盘上被写入那一行所指版本**的入口指纹（`sha256 of flash_firmware.py v…` 注释
   只在写得出来时参与比对——`b7c8d9e0f1a2` 的合法形态是「same content across v1.0.0/v1.0.1」，
   单一版本注释不是必需）。历史缺陷文件不可改，故两条已知项进豁免表，但豁免**必须配补偿控制**：
   表里每条都要 (a) 缺陷签名与登记表逐字相符（签名一变即红），(b) 被修复迁移实际处理，
   (c) 修复迁移写的常量与历史文件逐字相同。
3. **`backend/scripts/check_seed_identity.py`**：空库迁移完成后，用**扫描自己的**
   `_iter_script_entries` / `sha256_file` 对拍 `script` 表每一行——即 issue 建议的
   「upgrade head 后跑一次 scan 断言 `conflicts == []`」，但不启服务、不鉴权、不写库。
   另加点名回归（1.3.7/1.3.8/1.3.9 三行都在且各带各的真值，缺行这件事性质 1 表达不了）。
   挂在 required check `pr-migrate-empty-db` 新增的第 3 步，本地由
   `tools/dev/check_pr_migrate.py` 步骤 3 等价复刻（#825 的 gate-parity 口径）。

不校验 `nfs_path` 的绝对前缀：它是宿主侧路径，站点按 `STP_SCRIPT_RUNTIME_ROOT` 锚定；
只校验版本目录段，那才是与 sha 错位的那一面。

## Alternatives

- **直接改两条历史 seed 文件**：否决。#2258 之后已合入 main 的 revision 不可变；对已经
  `upgrade` 过 head 的库，改后的函数体永远不会再执行（`alembic_version` 仍是 head，
  `check_alembic_at_head.py` 的等值判定照样判绿）——正是 #2258 立门禁要防的形态。
- **让 scan 自动修 sha（把 `force_rebaseline` 默认化）**：否决。那道人工闸门的存在意义就是
  「已发布版本的内容身份变了必须有人签字」，默认化等于把 #751/#1276 类事故的报警器拆了。
- **在修复迁移里读磁盘算 sha**：否决。Agent 脚本树按站点挂载，控制面进程未必看得见；
  迁移只写常量，「常量对不对」由静态门禁与磁盘对拍负责。
- **只修 1.3.8 的 sha、不补 1.3.9 行**：否决一半。sha 修好后 scan 会自己补出 1.3.9（dev 里
  那行 id=68 就是这么来的），但「seed 链产出哪些行」本身就是被断言的对象——不补行就无法把
  「链该产出什么」变成判据，下次同类 off-by-one 还是只能靠人眼。
- **补行设 `is_active = true`**：否决，理由见 Decision 第 1 条第三点。
- **门禁只放 `backend/tests/services/`**：否决。该目录只在夜间全量 job 跑（#1569 的同一洞），
  而本单判据的现场就是「空库自举」，必须进 PR 路径。
- **像 `check_schema_sync` 那样维护 baseline 白名单**：暂不需要——空库对拍当前 47/47 全一致，
  没有历史噪音；若日后出现合法偏差再引入，并说明为何不能就地收敛。

## Verification

空库 before/after（postgres:16 一次性容器，`testcontainers`；未触碰 dev 应用库与生产库）：

```
$ alembic upgrade d4e5f6a7b8c9      # 今天空库自举的终点
('1.3.8', '06fa9fa39cd9…', '…/flash_firmware/v1.3.9/flash_firmware.py', is_active=False)
（无 1.3.9 行）
$ python -m backend.scripts.check_seed_identity            → rc=1，3 处
  - flash_firmware@1.3.8 行内 sha=06fa9fa39cd9… != 磁盘入口 2fad6bab2803… → scan 会判 conflict 且不会自愈
  - flash_firmware@1.3.8 的 nfs_path 不含版本目录段 /v1.3.8/
  - 缺 flash_firmware@1.3.9 行
$ alembic upgrade head              # 应用 e5f6a7b8c9d0
('1.3.8', '2fad6bab2803…', '…/v1.3.8/…')   ('1.3.9', '06fa9fa39cd9…', '…/v1.3.9/…')
$ python -m backend.scripts.check_seed_identity            → rc=0，47 行一致 / 覆盖 178 个磁盘版本
$ downgrade d4e5f6a7b8c9 && upgrade head（第二次）          → 结果逐字相同（幂等）
```

静态门禁的破坏性对照（7 处，一律 `/tmp` 备份 + 还原，不用 `git checkout`）：

| 变异 | 打红 |
|---|---|
| 把一条正常样本改成 off-by-one（模拟新增第三条） | 2 例（三面同源 + 磁盘对拍），且不在豁免表 |
| 正常样本的 `_CONTENT_SHA256` 改脏 | 磁盘对拍 1 例 |
| 删掉修复迁移 | 豁免失效 3 例 |
| 修复迁移把补行置 active | active 判据 1 例 |
| 修复迁移的 sha 与历史文件脱钩 | 2 例 |
| 改任一版本参数 | 「三版参数相同」前提 1 例 |
| 豁免表塞入未登记项 | 补偿控制 1 例 |

实跑命令与结果：

- `env -u TESTING -u JWT_SECRET_KEY -u DATABASE_URL pytest tests/test_seed_revision_version_guard.py -q` → **41 passed**
- `python tools/dev/check_pr_migrate.py`（真起 postgres:16 一次性容器，三步全跑）→ **[OK] 空库迁移 + schema 比对 + seed 身份对拍通过**
- `python tools/dev/check_pr_migrate.py --self-test` → [OK]（env 构造 / SKIP 语义 红绿双向）
- `env -u TESTING -u JWT_SECRET_KEY -u DATABASE_URL pytest tests/ -q --ignore=<两个容器用例>`（CI 同款 PR 子集）→ **1269 passed**（基线 1228 + 本单 41）
- `alembic heads` → 单一 head `e5f6a7b8c9d0`（`tests/test_alembic_heads.py` 在 PR 路径守住）
- `env -u DATABASE_URL -u TEST_DATABASE_URL TESTING=1 JWT_SECRET_KEY=ci python scripts/run_gates.py check:pr` → **[OK] check:pr (19 gates)**（含 layering / invariant-diff / pollution / immutability / **alembic-immutability**（只新增 revision，未碰历史）/ ip-leak / agent-tests / pr-migrate）

## Revisit

- **#942 是否重开**：两条历史迁移「行已存在就覆盖 `default_params`」的语义仍在（不可改）。
  当前 v1.3.7/1.3.8/1.3.9 三版参数字节相同 → 无实害，且该相同性已被门禁钉住；但**下一次谁给
  某个版本改参数**，这个原地覆盖就会真的把前一版参数改掉。真要收口得单独裁决（本单不动
  历史 revision，也不新增对 `default_params` 的写入）。
- **v1.3.9 修复后是 `is_active=false`**：这与「链本应产出的状态」一致（1.3.10 才是当前活跃版）。
  若产品判断 1.3.9 应当可派发，走显式启用端点，而不是回头改迁移。
- **CI 步骤 ↔ 本地 gate 步骤无测试钉住**：本单靠两侧注释互相指认。若这条对应关系再被破坏一次，
  值得补一条 parity 门禁（同 `tests/test_lock_order_pr_path_contract.py` 的思路：命名判据 ∪ 登记表）。
- **`check_seed_identity` 的性质 1 只覆盖「已在库里的行」**：磁盘 178 个版本里绝大多数没有行
  （未 scan），所以「缺行」目前只对 `REQUIRED_ROWS` 点名的三行成立。要把「该登记却没登记」
  变成全局判据，得先定义「哪些版本必须被 seed 登记」——那是 #735（脚本版本膨胀与退役闭环）
  的范围，不在本单扩大。
- 本单不碰 `#738`（双端 validator 重复）、`#739`（431 处静默异常吞咽）等既有债务。
