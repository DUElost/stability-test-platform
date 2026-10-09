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
- **authority 判据（#3615 复核 P2 + 复审 R1–R3/S1–S3/T1–T2/U1–U2/V1–V2/W1–W3/X1–X2 返修，2026-10-07/09）**：
  - 资源根相对 Agent authority 的**全部片段**必须恰为 `("resources", <族子目录>)`——
    前/后缀均不允许；必须出现在**返回位置**且每个被返回路径值都符合 authority；
    「经局部变量返回错误 literal 根」与直接返回同判红；root 与 consumer 的
    project/variant/bundle 层分开；
  - 显式 override 必须**双通道**（同一消费者同时读取 param 与 env），且证明必须关联到
    **实际消费值**：守卫变量在该行的 reaching def 与 return 值来源逐一分类；被同名覆盖
    或未被使用的旧链不作证明；**每个消费点各自证明**（他点正确链不得放行本点的双键/
    条件表达式）；IfExp 仅对**完整空/非空选择语义**可证明形态分类——`X if X else Y`
    （preferred=body）与 `Y if not X else X`（preferred=orelse）；`X if not X else Y`
    空值时仍返回空 X → 拒绝（人工分类），不得标 Y 优先；两侧都带 override 键的非透明
    IfExp（含通道重叠）不得因 Name 回溯判 `none` 而跳过；键交集不得代替条件等价性；
  - fallback 惰性守卫校验**空/非空方向**且绑定**当前** override 值（`if` / `IfExp` /
    Or 左侧同判）：历史变量名在被清空后失效；`"" or default()` 左侧非当前 override →
    急切；仅对值语义可证明的有限形态放行——`(param or env) and ""` 等 And/比较变换
    不得凭键标记冒充活值；有惰性 fallback 时最终 return 必须关联默认根结果，否则
    「空 override 丢弃默认 authority」；仅 `not X`→body 或 `X`→orelse 等可证明形态
    放行；`if X: default()` / `default() if X else X` 反转方向判急切；复合条件无法
    证明 → 人工分类；
  - 同族导入索引覆盖**函数体内** Import/ImportFrom 并保留原始符号名；同一别名多来源
    保留**全部**候选并显式报人工分类（禁止 last-write-wins）；`import *` / `getattr`
    动态引用一并判红；
- `--base` 只做增量防新增与 legacy 例外防扩张（head 例外集必须是 base 子集），不替代
  全量 census；base ref 不可解析或 base 契约坏 JSON → 退出 2「不可验证」，不当空 diff 成功；
- 顺序：`--self-test` → 全量 → `--base`；退出 1 = 判据违例、2 = 不可验证。

**B4-G2-r9（2026-10-09，draft，待独立复核）**：在同一 checker 内对四专项非 legacy
host-local 资源值做统一有限值分析。本轮不是 G2 验收，也不推进 B4 激活。

- 值域：P（非空 param 路径）、E（非空且不同于 P 的族 env 路径）、D（形状校验过的默认根）、
  EMPTY、UNKNOWN。UNKNOWN 带文件、函数、行和不可证明节点/原因；
- 四格在每个实际消费点分别证明：param+env → P；仅 param → P；仅 env → E；双空时
  `resources_dir` 的实际消费路径 → D，config 资源字段 → EMPTY。非空 override 的每条
  可达路径都不得调用默认根，即使结果被丢掉；某处调用了 D 不能代替「被消费的那条路径是 D」；
- 支持集：简单资源赋值/传播、已验证的 param/env 读取、Or 短路、简单真值与 `not` 的
  If/IfExp、Return、校验过的默认根 helper、最终 Path 包装、既有 project/variant 后缀。
  正确否定 `Y if not X else X` 与正向 `X if X else Y` 按真实值语义。`Path("")` 是相对目录，
  不是 D。读取与 helper 必须落到真实定义或已验证的有限摘要，不按名字信任 env /
  param_or_env / 默认根。D 只来自校验过的 authority 构造 helper；
- config 归一化（gpu/powercycle/sleep config）只证明资源字段 P > E > EMPTY。其它 dict
  标志、数字、Compare 不进入资源投影，避免误报。四个 `resources_dir` 才证明最终路径
  P > E > D；
- 预算：表达式/Name 深度 64，同文件 helper 深度 8，每函数每输入格同时路径状态至多 32。
  超预算、递归、影响资源流的循环必须人工分类且非零，禁止截断成绿。checker 不
  exec/eval/import 被扫描源码；
- 三项证明不变量集中闭合：每个可达返回/消费点都要满足（禁止 `any()` 用另一返回点放行）；
  先求值 RHS 再绑定名字，Name 只读引用时点已经执行的赋值（`selected = base` 定格当时的值）；
  变换与 UNKNOWN 沿透明 Name 传播，不得降级成键标记。`_key_markers`、`proven_points`、
  「任一 return 命中默认根」只可诊断。有限值已通过时不再用句法惰性追加放行或误伤；
  证明失败时保留历史选择顺序与惰性诊断。And、Compare、未知调用、对资源值的下标保持
  UNKNOWN，并穿过 Name 仍是 UNKNOWN。

**影响面（r9 只改这三个文件）**：`tools/dev/check_resource_anchors.py`、
`tests/test_check_resource_anchors.py`、本 Note。不改契约、`run_gates`、CI、族树、
manifest、template、AGENTS 或生产配置。历史影响面（契约与 gate 接线，本轮未改）：
`tools/dev/check_resource_anchors.py`、`tools/dev/resource_anchor_contract.json`、
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
- **继续用键标记、任一 return、按最终 return 行号回溯赋值（r9）**：弃。Y1 用正确返回
  放行错误返回，Y2 把 fallback 前的快照看成最终 `base`，Y3 在 Name 边上丢掉 And。
  这三处都会把错误值或非法默认根调用判绿。
- **按族或函数名白名单，或把 UNKNOWN 降级后放行（r9）**：弃。支持集内的错值与非法
  默认根调用不能绿；支持集外且影响资源的输入保持人工分类 / 非零。
- **另写运行时解释器替换 AST checker（r9）**：弃。方案是同一 checker 内的一个有限值
  过程。测试里的真实函数 oracle 独立 exec 抽出的函数，不与 checker 共享实现，也不
  import 设备入口。

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
- **第五轮 U1–U2 的返修证据**（逐字反例，修复前 `errors=0`）：
  U1 保留正确 `param or env` 与 `if not base` 后追加
  `base = env(...) if env(...) else cfg.get(...)` → 「消费值选择链 env 先于 param」
  （他点正确证明不得放行）；U2 仅改 `if not base:` → `if base:` → 「急切求值」；
  等价 `if base: pass else: default()` 保持绿。
- **第六轮 V1–V2 的返修证据**（逐字反例，修复前 `errors=0`）：
  V1 `return Path(param if not param else env) / project` → 「含双键但无法证明选择顺序」
  （`X if not X else Y` 完整选择语义不成立）；V2 在正确 fallback 后追加
  `base = default() if base else base` → 「急切求值」；等价
  `default() if not base else base` 保持绿。
- **第七轮 W1–W3 的返修证据**（逐字反例，修复前 `errors=0`）：
  W1 `return Path(env if not env else param) / project` → 「无法证明选择顺序」；正确否定
  等价写在 **base 赋值**（fallback 前）保持绿；W2 在正确 fallback 后追加
  `combined = param or env; base = env if env else combined` → 「无法证明选择顺序」
  （对齐包 SHA 后三项守卫仍只资源 checker 红）；W3
  `base = ""; base = default() if not base else base` 与 `base = "" or default()` →
  「急切求值」（惰性证明绑定当前 override）。
- **第八轮 X1–X2 的返修证据**（逐字反例，修复前 `errors=0`）：
  X1 保留 canonical fallback、仅替换最终 return 为 `Path(env if not param else param)`
  → 「最终返回未关联默认根 fallback」；选择位置的否定等价（G7）保持绿；X2
  `base = (param or env) and ""; base = default() if not base else base` → 「急切求值」
  （And 清零不得凭键标记作活值；对齐包 SHA 后三项守卫仍只资源 checker 红）。
- `python tools/dev/check_resource_anchors.py`：真实仓库绿（69 文件 / 35 族；候选 49；
  26 锚 / 50 定位点；legacy 10；未解析 0），离线 <1s。
- `python tools/dev/check_resource_anchors.py --base origin/main`：绿（首次引入契约，NOTE）。
- `python scripts/run_pytest.py tests/test_check_resource_anchors.py -q`：聚焦测试
  （含别名导入 / 函数内别名导入 / 别名复用多来源 / 通配+getattr / 错误返回根 / 前/后缀
  多余片段 / 同名覆盖 / 变量返回 literal / 条件表达式 env 优先 / 守卫方向反转 /
  否定 IfExp 无法证明 / 最终 return 丢弃默认根 / 正确否定等价（选择位）/
  IfExp 非空分支 / 变量双键 IfExp / 失效变量名守卫 / And 清零冒充活值 /
  删 param / env 优先 / 变量优先 / 未使用链掩盖
  （红绿双侧）等隔离变异，均含恢复后转绿）。
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
- **r9 验证（baseline `origin/main` @ `61b51819780b1ed3ec0ee9ec430ec2b16e4fe10f`，
  本工作树项目入口，2026-10-09）**：
  - 修复前 Y1/Y2/Y3 逐字变异的完整 checker 均为 exit 0（69/35/26/legacy 10/未解析 0）。
    修复后同一形态非零，归因是「有限值证明」，不是族源码改写；
  - 现行四个 `resources_dir` 四格均为 `(P, P, E, D)`；`gpu_config` /
    `powercycle_config` / `sleep_config` 的资源字段均为 `(P, P, E, EMPTY)`；
  - 固定组合矩阵与独立 oracle 在 `tests/test_check_resource_anchors.py`：
    期望字面量不由分析器计算；oracle exec 抽出的真实函数，不 import 设备入口，
    不与 checker 共享实现。Y1 空 override + `use_alternate` 运行时得到相对 `demo`；
    Y2 fallback 前快照得到相对 `demo` 且默认根仍被调用；Y3 非空 param 仍落到默认根。
    fallback 后快照、正确否定、非空提前返回在 oracle 与 checker 均为绿；
  - 三项规则回退后对应测试变红，再按 SHA-256 恢复 checker：
    `any()` 放行使 `dual-good-bad` 变成 `(P, P, E, D)` 且 Y1 scanner exit 0（pytest exit 1）；
    未来赋值使 `snapshot-before` 的 empty 格变成 `D` 而不再报 `有限值证明[empty]`（pytest exit 1）；
    UNKNOWN 经 Name 降级成 P 后 `and-name-1` 变成 `(P, P, RED, RED)`，报告不再含 UNKNOWN
    （pytest exit 1）；
  - Y3 隔离副本：变异后 `check_script_packages` 与资源 checker 都红；
    `--register mtbf_setup 99.0.0`（sha `1e88ce0ba1c3`）之后
    `check_tool_manifest --base origin/main` exit 0、`check_script_packages` exit 0、
    资源 checker 仍 exit 1（A01 四格，含 `And 变换` / UNKNOWN / 非空 override 仍执行默认根）。
    恢复 `_lib.py` 与 manifest 后三项都 exit 0。登记只发生在该副本；
  - 同一次命令序列：`--self-test`、全量 census、`--base origin/main` 均为 exit 0，
    census 仍是 69 文件 / 35 族 / 26 锚 / legacy 10 / 未解析 0；
    `run_pytest.py tests/test_check_resource_anchors.py -q` 77 passed；
    ruff 通过；`check:quick` 16 gates OK；`run_pytest.py tests/ -q`
    2406 passed / 18 skipped；`check_script_packages` 与
    `check_tool_manifest --base origin/main` exit 0。
    六项 required CI 在最终 head 上另核，未绿之前不算 CI 完成。
    本记录不是 G2 验收。

## Revisit

- 新增/晋升脚本族出现新的资源 authority 形态（如新工具包族、新的族级资源子目录）时：
  按 §1.4 流程更新契约、必要时扩展有限表达式覆盖面；
- 10 项 legacy helper 因真实需求升版本时清理/收敛，并同步删除契约中的 legacy 条目
  （契约只许收缩例外，扩张判红）；
- 若 checker 出现误报/漏报（例如未来出现动态表达式导致「需人工分类」频繁），先修
  有限契约与分类，不引入通用静态分析框架（方案 §7.9 退回条件）；
- #3498 若推进到改变脚本包/authority 分层，本 checker 的扫描面与契约需随之重审。
- **r9 未验证边界**：任意 Python、新的 authority 类型、新运行时、支持集外且影响资源值的
  形态。这些必须显式人工分类 / 非零，不要求判绿。若当前真实合理形态无法被支持集覆盖，
  交最小反例并退回，不按族/函数名放行，也不放宽 UNKNOWN。
- r9 停在独立复核。Reviewer 通过之前，仓库交付不等于 G2 验收，不发布、不部署、不 scan、
  不重指 Plan。
