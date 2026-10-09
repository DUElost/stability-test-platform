# B4/G2 资源 authority 离线守卫（#3321 / #3601 v1.1 C1）

Status: implemented
Class: testing

## Decision

新增 `tools/dev/check_resource_anchors.py`（+ 最小契约输入
`tools/dev/resource_anchor_contract.json`），并按「最小扩展既有检查位置」的方式接入
`scripts/run_gates.py` 的 `tool-manifest` gate 与 `.github/workflows/ci.yml` 的
ADR-0033 tool_manifest step（不新增 gate profile / required job）。

**判据（#3601 v1.1 §1.2 C1）**：authority 由资源所属通道决定，不能由消费者包位置决定。

- 四专项 APK 默认根 = 既有 Agent 代码根 `config.AGENT_DIR/resources/<族>`（G1a 已修），
  fallback 分支惰性导入 `config`；override（param > env）显式路径原样消费、空串进默认；
- 工具包根走 ADR-0051 D7 `requires_tools` 绑定（合法落在 tools_cache，版本真源仍是
  `tool_manifest.json` + `backend/agent/tool_requirements.py`，契约不复制版本清单）；
- 包内伴随文件以本包根定位且须真实入包；import/schema/运行目录/部署源按类登记，不做
  固定目录或「出现 env 即绿/即红」的判定。

**行为**：

- 全量 census 恒跑：独立发现 `__file__`/AGENT_DIR 派生路径锚与 family 资源键消费者，
  与声明集双向对拍。未声明候选、声明条目无法在源码复现、无法解析的被消费表达式、
  缺失源文件、零候选/零覆盖均非零退出；项目键等非资源键不扩张候选面；
- 有限表达式求值 + 同文件 helper 调用回溯（`__file__` → variable/parent/join/relative
  tuple → helper → 消费者），不做任意 Python 数据流；无法确认的表达式报「需人工分类」；
- §1.4 的 26 项按 id 落进契约：4 项本批修（G1a 已修、保留历史追溯）、5 项当前安全、
  7 项非本形态、10 项函数级 legacy（A02/03/05/06/08/09/11/12/17/18）。legacy 只按
  函数与真实调用关系登记，引用 #3320 并写明删除/重新判定条件；新增入口调用（**含别名
  导入、`import *`、`getattr` 动态引用**）、导入路径、`__all__` 导出或资源消费使其可达
  即红；禁止文件/族目录级白名单；
- **authority 判据（#3615 复核 P2 + 复审 R1–R3/S1–S3/T1–T2 返修，2026-10-07/09）**：
  - 资源根相对 Agent authority 的**全部片段**必须恰为 `("resources", <族子目录>)`——
    前/后缀均不允许；必须出现在**返回位置**且每个被返回路径值都符合 authority；
    「经局部变量返回错误 literal 根」与直接返回同判红；root 与 consumer 的
    project/variant/bundle 层分开；
  - 显式 override 必须**双通道**（同一消费者同时读取 param 与 env），且证明必须关联到
    **实际消费值**：守卫变量在该行的 reaching def 与 return 值来源逐一分类；被同名覆盖
    或未被使用的旧链不作证明；实际值只读 env / 顺序反转 / 多条候选 → 红或人工分类；
  - 同族导入索引覆盖**函数体内** Import/ImportFrom 并保留原始符号名；同一别名多来源
    保留**全部**候选并显式报人工分类（禁止 last-write-wins）；`import *` / `getattr`
    动态引用一并判红；
- `--base` 只做增量防新增与 legacy 例外防扩张（head 例外集必须是 base 子集），不替代
  全量 census；base ref 不可解析或 base 契约坏 JSON → 退出 2「不可验证」，不当空 diff 成功；
- 顺序：`--self-test` → 全量 → `--base`；退出 1 = 判据违例、2 = 不可验证。

**影响面**：`tools/dev/check_resource_anchors.py`、`tools/dev/resource_anchor_contract.json`、
`tests/test_check_resource_anchors.py`、`scripts/run_gates.py`（tool-manifest 项内追加两命令）、
`.github/workflows/ci.yml`（同一 step 追加两命令）。不新增 script 版本、不改 template/Plan、
不动四个 setup 与 check/finish 族树、不修改 G1a 三条临时 EXCEPTIONS。

## Alternatives

- **把候选写死成 26 行清单（关键词/文件名单）**：弃。窄名单会让新族/别名 helper 静默
  消失，且「出现 env 即绿/即红」与 C1 冲突；改为「独立发现 + 声明双相对拍」。
- **用固定目录（agent/resources 绝对路径）当全局正确答案**：弃。四类 authority 中
  tools_cache 工具根、包内伴随文件、显式中心存储 override 都合法，固定目录必然误报。
- **实现通用 Python 数据流分析**：弃。按方案 §1.5 治理预算，只做覆盖已知形态的有限
  表达式与调用回溯；无法确认即报人工分类，不猜。
- **新建 gate profile / required job / Registry 字段**：弃（方案 §1 治理成本上限）。
  复用既有 tool-manifest 检查位置与 CI step，命令逐字一致。
- **在 checker 里维护工具版本/包成员副本**：弃。版本真源仍读 manifest；`requires_tools`
  形态复用 `tool_requirements.py` 按路径加载（与 `check_script_packages` 同单源）。
- **`--base` 缺契约时判红**：弃。本 PR 首次引入契约，base 无文件是「无先前状态」而非
  解析失败（与 `check_tool_manifest` 对首次登记的同判据）；ref 不可解析/JSON 坏才红。

## Verification

- `python tools/dev/check_resource_anchors.py --self-test`：红绿双向自证（隔离 fixture）——
  旧深度 fallback / 别名 / join / relative tuple / cache 祖先 / dead 接入 / 别名导入接入 /
  函数内别名导入 / `import *` 接入 / `getattr` 接入 / `__all__` 导出 / 绑定缺失 / 错误 env /
  成员缺失 / 未声明候选 / 未解析表达式 / 零候选 / 急切默认锚 / 错误返回根 / 额外错误目录 /
  多余后缀 / 片段错序 / 删 param override / env 先于 param / 变量 env 优先 / 例外扩张 /
  base 不可验证 → 红；agent-dir 形态 / 显式 override / 变量 param 先行 / `parents` 与
  tools_cache 字样不误报 → 绿。
- **#3615 复核三项 P2 的返修证据**（隔离副本，修复前全绿→修复后全红）：
  ① `from _lib import resources_dir as rd` + `rd({})` → A02 可达 + 别名导入路径；
  ② `_good = ...; return Path("/tmp/incorrect")` → 「未出现在返回位置 + 返回值不符合
  authority」；③ 删 param 只留 env → 「显式参数 override 通道被删除」；另加固
  env 先于 param → 「显式参数优先语义被反转」。
- **复审 R1–R3 的返修证据**（逐字反例，修复前 `errors=0`）：
  R1 `return Path(AGENT_DIR)/"resources"/"mtbf"/"unexpected"` → 「片段必须恰为 …
  （前/后缀均不允许）」（root + 继承消费者 + 返回位置三处归因）；
  R2 `def main(): from _lib import resources_dir as rd; rd({})` → 「现可从族入口到达」+
  「以别名导入 legacy helper」；R3 `base = env(...); param_value = cfg.get(...); base = base or
  param_value` → 「override 选择链 env 先于 param」；等价变量 param-first 写法保持绿。
- **第三轮 S1–S3 的返修证据**（逐字反例，修复前 `errors=0`）：
  S1 `AGENT_DIR/"unexpected"/"resources"/"mtbf"` → 「片段必须恰为 ('resources', 'mtbf')
  （前/后缀均不允许）」；S2 两个函数复用别名 `rd`（`resources_dir` / `sha256_file`）→
  「现可从族入口到达」+「同一别名多来源（需人工分类）」；S3
  `unused = param or env; base = env or param` → 「override 选择链 env 先于 param（L144）」
  （未使用链不再作为证明）。
- **第四轮 T1–T2 的返修证据**（逐字反例，修复前 `errors=0`）：
  T1 `base = param or env; base = env`（正确链被同名覆盖）→ 「消费值只读取 env——param 未
  参与实际选择（被覆盖/未消费）」；T2 `wrong = Path("/tmp/incorrect"); return wrong / project`
  → 「经局部变量返回硬编码路径」；对照保留正确 param-first 的写法保持绿。
- `python tools/dev/check_resource_anchors.py`：真实仓库绿（69 文件 / 35 族；候选 49；
  26 锚 / 50 定位点；legacy 10；未解析 0），离线 <1s。
- `python tools/dev/check_resource_anchors.py --base origin/main`：绿（首次引入契约，NOTE）。
- `python scripts/run_pytest.py tests/test_check_resource_anchors.py -q`：30 passed
  （含别名导入 / 函数内别名导入 / 别名复用多来源 / 通配+getattr / 错误返回根 / 前/后缀
  多余片段 / 同名覆盖 / 变量返回 literal / 删 param / env 优先 / 变量优先 / 未使用链
  掩盖（红绿双侧）等隔离变异，均含恢复后转绿）。
  隔离副本变异证明 checker 有牙齿（恢复 G1a 错根 / 撤工具绑定 / 删包内伴随文件 /
  接入 dead helper / 抽掉声明条目 → 红；恢复 → 绿）；接线用例读 `run_gates.GATES`
  结构断言 tool-manifest 项含 `--self-test` 与 `--base`，CI 同 step 同命令。
- 接线有牙齿（隔离 clone 实测，见 PR 正文）：在 clone 内恢复 `mtbf_setup` 旧 `parents[3]`
  错根并 `--register mtbf_setup 1.4.3` 让包 sha 对平后，`check_tool_manifest --base origin/main`
  与 `check_script_packages` 均绿，**仅** `check_resource_anchors --base origin/main` 红，且
  归因是资源 checker 自身（A01 深度 4 / `('resources','mtbf')`）；`git checkout` 恢复后转绿
  （`git status` 空）。红不是来自前置 package sha 检查。
- 通用门禁：`check_script_packages.py`、`check_tool_manifest.py --base origin/main`、
  `run_gates.py check:quick`、`run_pytest.py tests/ -q`，以及 PR head 六项 required CI。

## Revisit

- 新增/晋升脚本族出现新的资源 authority 形态（如新工具包族、新的族级资源子目录）时：
  按 §1.4 流程更新契约、必要时扩展有限表达式覆盖面；
- 10 项 legacy helper 因真实需求升版本时清理/收敛，并同步删除契约中的 legacy 条目
  （契约只许收缩例外，扩张判红）；
- 若 checker 出现误报/漏报（例如未来出现动态表达式导致「需人工分类」频繁），先修
  有限契约与分类，不引入通用静态分析框架（方案 §7.9 退回条件）；
- #3498 若推进到改变脚本包/authority 分层，本 checker 的扫描面与契约需随之重审。
