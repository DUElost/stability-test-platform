# 种子治理门禁：判据从「整文件子串」改为 AST 识别真实停用形态（#2527）

Status: implemented
Class: bug-fix

- 日期：2026-09-17
- 相关：`#2055` / `#942`（门禁的立法来源）、`#2399`（被误判的迁移）、`#2386`（发现场景——跑根目录 `tests/` 时撞见）

## Decision

### 1. 判据改为 AST，只认「停用既有版本」的两种真实形态

原判据是 `"is_active = false" in <整文件>`，两头都错：

| 形态 | 旧判据 | 新判据 |
|---|---|---|
| `UPDATE script SET is_active = false …`（有空格） | ✅ 命中 | ✅ 命中 |
| `UPDATE script SET is_active=false …`（**无空格**） | ❌ **看不见**（假阴性） | ✅ 命中 |
| ORM `obj.is_active = False` | ❌ **看不见**（假阴性） | ✅ 命中 |
| `INSERT … VALUES (…, false, …)`（登记非活跃版本） | ❌ 误判（假阳性） | ✅ 不判 |
| docstring / 注释里提到该串 | ❌ 误判（假阳性） | ✅ 不判 |

新实现 `deactivation_sites(source)`：`ast.walk` 收集
① 字符串常量里匹配 `\bupdate\b…\bset\b…\bis_active\s*=\s*false\b`（按序、有界窗口、大小写不敏感）；
② `ast.Assign` 到 `.is_active` 且值为 `False`。**不是**子串扫描——
同族教训在 `tests/test_alert_metric_producers.py` 头注（子串匹配被注释骗过 → 改 AST）。

合入 main（#2526）时保留其加强项，不削弱本单判据：排除模块/函数/类 docstring 常量节点；
f-string（`JoinedStr`）取字面量片段拼接；`_has_deactivation(path)` 作语料守卫薄封装。

### 2. 修好判据后浮出的存量：一条 legacy，按既定口径登记

`i9j0k1l2m3n4_seed_gpu_setup_v104_stable_install.py`（2026-08-31，legacy 窗口内）写的是
`SET is_active=false`（**等号两侧无空格**）→ 旧判据 0 命中，**完全看不见它**。新判据看见后它成为
唯一新增的 offender，按 `_LEGACY_SEEDS_WITHOUT_REF_CHECK` 的既定口径登记（理由同该表注释：
追溯改造会改变**全新安装**的行为——引用存在时直接中止部署），并把「为什么这条是现在才出现」
写进表注释。

**只多出这一条**（已逐文件核对过带 `is_active=false` 的全部 6 个文件：其余 4 个要么带引用检查、
要么是 #2055 已收口的形态、要么是 INSERT 形态）。

### 3. 不做

- **不追溯改 legacy 迁移**：见 §2。
- **不改「停用前必须检查引用」这条业务规则本身**：本单只修**判据怎么识别**，规则不变。
- **不动 `e5f6a7b8c9d0`（#2399）**：它本就合规（只 INSERT 一行非活跃版本），迁移不可原地改。

## Alternatives

- **直接给 `e5f6a7b8c9d0` 补引用检查**：它没有停用任何既有版本，加检查是无的放矢；且迁移已合入，
  改它要附重放迁移。否。
- **把 `e5f6a7b8c9d0` 塞进豁免表**：那会把「合规文件」与「legacy 缺检查文件」混为一谈，
  而豁免表的语义是「历史上确实缺检查」。否。
- **判据改成「更宽松的整文件正则」**（`is_active\s*=\s*false`）：能修假阴性，但假阳性（INSERT/docstring）
  照旧。否。

## Verification

- `pytest tests/test_script_seed_governance.py -q` → **11 passed**（新增 5 条判别力用例：
  INSERT+docstring **不判** / 有空格 UPDATE 判 / **无空格 UPDATE 判** / ORM 判 / ORM `= True` 不判）。
- **红向反证**：把判据退回旧子串版 → **4 failed**（INSERT 假阳性、无空格假阴性、ORM 假阴性，
  以及真实仓库用例因为又去点名 `e5f6a7b8c9d0`）；还原即绿（11 passed）。
- 真实仓库面：`_seed_files_with_deactivation()` 现在只把**该看的**挑出来——全量 offender 为 0
  （`i9j0k1l2m3n4` 已按 legacy 口径登记）。
- `pytest tests/ -q`（根目录全量）→ 见 PR（本单同时清掉 main 上那条常红）。
- `run_gates check:quick` → **10 gates 绿**；`ruff check` 全绿。

## Revisit

- **门禁的可见性**：这条守卫此前在 main 上红了很久（无人收口），说明**根目录 `tests/` 不在
  required checks 里**（required 为 `lint`/`CodeQL`/`pr-typecheck`/`pr-compileall`/`pr-agent-tests`/
  `pr-migrate-empty-db`）。是否把「根目录契约测试」纳入 PR 路径属 CI 分层决策，另立单更清楚。
- **同类子串判据的排查**：本单只修了种子门禁这一处。仓库里若还有「整文件子串」形态的判据，
  会同样两头失真（假阳性 + 假阴性）——出现第二例时值得按同一把尺子过一遍。
- **豁免表的终态**：随 `#735` 零引用版本退役自然收敛（表注释已写明），本单不改变该出口。
