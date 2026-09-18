# admin-only 读接口棘轮的扫描器改 ast 解析：路径换行的装饰器不得成为闸门盲区（#2642）

Status: implemented
Class: testing

## Decision

`tests/test_admin_only_read_surface_register.py` 是 #2418/#2360 留下的棘轮：扫出「GET +
`Depends(require_admin)`」的端点，逐条要求登记「普通用户为什么不会走到它吃 403」。
本单修的不是登记表，而是**扫描器自身的覆盖面**——它原先是逐行文本状态机：

- 用 `_ROUTE_RE.match(line)` 认装饰器第一行，路径写在**下一行**时整条装饰器被丢弃；
- 丢弃是静默的：该端点既不进 `_REGISTERED` 的比对集合，也不进结果字典；
- 于是第 3 条反向检查（「登记项若在扫描结果里消失则报错」）**同样拦不住**——它只看
  「已登记的是否还在」，从未登记过的端点根本不在它的视野里。这不是推断，M1b 实测为绿。

改成 `ast` 解析（`iter_route_endpoints`）：对每个路由文件建 AST，遍历
`FunctionDef`/`AsyncFunctionDef` 的 `decorator_list`，认 `@router.<verb>(...)` 调用，
返回 `(文件名, 动词, 路径, 行号, is_admin)`。语义判定 `_endpoint_is_admin` 只看
**装饰器 args/keywords 与函数签名的 defaults/annotations**，明确不看函数体，覆盖三种写法：
`= Depends(require_admin)`、`Annotated[..., Depends(...)]`、装饰器里的
`dependencies=[Depends(require_admin)]`。`admin_only_get_endpoints` 对外形状不变
（`dict[str, int]`），登记表与既有 5 条判据无需改写。

新增两条防线，让「扫描面萎缩」不再是静默失败：

1. `test_scan_is_discriminative` 从 2 种排版扩到 9 种，其中 3 种是换行写法
   （路径换行、参数换行、`Annotated`、`dependencies=`、body 干扰、末个端点后无 `def`）；
2. `test_two_parsers_agree_on_the_real_tree`：`ast` 解析条数必须等于
   `count_route_decorators` 的正则字面计数（真树上 197 == 197）。两套实现失明方式不同
   ——ast 看不见「装饰器不挂在函数上」（动态注册、`getattr(router, verb)`），正则看不见
   语义——交叉核对能在扫描面萎缩时立刻红，而不是少一个端点。

登记表因此多出一条**本来就存在但从未被看见**的端点：`logs.py:/log-signals/orphans`
（写法是路径换行）。已核实其前端只有 `frontend/src/utils/api/types.ts:1664` 的类型镜像、
零调用方，结论良性；登记它是因为闸门必须承认它，不是因为它是问题。

## Alternatives

- **只给旧状态机补换行续行**：否决。它仍然是「按文本形状猜语义」，下一次换行位置变化
  （`dependencies=` 换行、注释夹在中间、字符串里带引号）会再造一个同型盲区；`ast` 是本仓
  已有依赖（标准库）且判定口径唯一。
- **给 `backend/api/routes/*` 加运行时断言**（遍历 `FastAPI` 路由表比对登记表）：口径最硬，
  但会把棘轮变成需要 DB/app 装配的接口层用例，且要重新回答「哪个 `APIRouter` 前缀对应哪个
  文件」。本单要修的是解析盲区，运行时对拍可留作后续（见 Revisit）。
- **加「扫描条数 ≥ 登记规模」下界**（issue 建议 3）：**有意未做**。它被正向检查
  （每条扫描项都要登记）与反向检查（每条登记项都要在扫描结果里）联合蕴含，写成第三条只是
  冗余断言；真正缺的是「未进入扫描集合」的暴露面，由 `test_two_parsers_agree_on_the_real_tree`
  补，而不是由一个数字下界补。
- **不改扫描器，只给 `logs.py` 那处改成单行写法**：否决。那是把盲区藏回原处，正是本单判据
  要防的行为。

## Verification

环境：本机隔离库（`env -u DATABASE_URL`、`PYTHONDONTWRITEBYTECODE=1`），未触碰生产库。

- 基线：`tests/test_admin_only_read_surface_register.py` → **7 passed**（原 5 条 → 7 条）。
- 覆盖面差异（**同一棵树**）：旧扫描器 **13** 条 / 新扫描器 **14** 条；注入一个「路径换行」的
  admin GET 后 旧 **13（看不到）** / 新 **15（看到）**。差值就是盲区的直接读数。
- 变异自证（每条都只改一处，跑完还原并重跑基线）：
  - `M1` 把换行写法的 admin GET 注入**真树** → `test_every_admin_only_get_is_registered` 红
    （闸门真的会拦下一个新写法端点）；
  - `M1b` 同一注入、**只跑反向检查** → 绿。这条是本单的判据核心：反向检查对「从未进入扫描
    集合」的端点无能为力，所以补交叉核对不是装饰；
  - `M2` `_endpoint_is_admin` 恒 False → **3 红**（登记表正向、判别力、非空面同时失守）；
  - `M3` 判定范围连函数体一起看 → **1 红**（`test_body_only_dependency_does_not_leak`）；
  - `M4` `count_route_decorators` 恒返 0（模拟第二实现失明） → 交叉核对 **1 红**。
- 门禁（都在**已提交**的树上跑，跑期间不写文件）：结果见本 PR 的后续评论 / CI。

## Revisit

- 若出现「装饰器不直接挂在路由函数上」的注册写法（动态 `router.add_api_route`、
  `getattr(router, verb)`、自定义装饰器包装），`ast` 方案会重新变成盲区，而正则计数也数不到
  ——那时该换成运行时对拍（遍历 app 路由表），而不是再补一条文本判据。触发条件：
  `count_route_decorators` 与运行时路由条数出现差值。
- 当前 `_endpoint_is_admin` 认 `require_admin` 这个名字。若将来出现第二种 admin 依赖
  （别名、`require_superadmin`、权限点函数），闸门会把它当非 admin 而**漏登**（方向是漏报不是
  误报）。届时应把「哪些依赖算 admin-only」提为登记表顶部的显式常量并配一条判据，
  而不是往匹配函数里塞第二个字面量。
- `logs.py:/log-signals/orphans` 的登记依据是「前端零调用方」。这条结论是本轮人工核实
  （`types.ts:1664` 类型镜像，无 fetch/hook），没有机器防线兜底：将来有人加调用方，
  本闸门不会变红。与 #2360 的同类风险一致，收敛办法仍是把「零调用方」类依据交给
  前端调用面扫描，属另一单。
