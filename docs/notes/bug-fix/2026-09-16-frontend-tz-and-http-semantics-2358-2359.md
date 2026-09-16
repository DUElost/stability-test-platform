# 前端「界面说的不是实话」批：#2358 时区双重转换 + #2359 403 误报为连接失败

Status: implemented
Class: bug-fix

## Decision

两条同属「UI 把排查方向带偏」：一条把时间显示成未来，一条把权限问题说成网络问题。

### 1. #2358：用户管理页的 naive 时间戳必须**原样**展示

**机制（本轮只读核对生产，钉死了成因）**：

- `backend/models/user.py` 的 `created_at` / `last_login` 是 `Column(DateTime)`——**naive** 列；
- 写入点 `backend/api/routes/auth.py:147` 传的是 `datetime.now(timezone.utc)`（aware）——
  PG 会把它按**会话时区**转成 `timestamp without time zone` **并剥掉偏移**，读回即**本地墙上时间**。生产实测：`max(last_login)=2026-09-16 18:10:15`，本地 `now()=18:32(+08)` 同刻度，
  而 UTC 当时是 `10:32`——若是 UTC 口径该值会落在「7.6 小时后的未来」，不可能；
- 前端 `formatDateTimeFull` → `parseIsoToDate` 对 naive 值补 `Z`（当 UTC）→ `toLocaleString`
  再 +8 → 页面上显示成「未来时间」。同表里 `host.last_heartbeat` / `device.last_seen` 是
  `DateTime(timezone=True)`（aware，读回带偏移）→ 那类字段走转换是**对的**。

**改法**：新增 `formatNaiveLocalDateTime()`（`frontend/src/utils/time.ts`）——只做形态归一
（`T`→空格、截断到秒），**不做任何时区换算**；`UserTable` 的两个时间列改用它。
aware 字段继续用 `formatLocalDateTime`（两者的分工写进了 docstring）。

**未做（有意）**：不动 `parseIsoToDate` 的全局语义（那是 aware/naive 共用的解析原语，改它
会波及所有页面）；也不改后端列类型（迁移面 + 历史数据口径，属另一个决策）。全局
「naive 契约」的收口列入 Revisit。

### 2. #2359：/wifi 的加载失败按 **HTTP 语义**分类

`WifiPage` 的 `isError` 分支写死「请检查后端服务连接」——403（user 角色打开 admin-only
页面）被报成连接问题，把排查方向引到后端/网络（同类先例 #955）。

**改法**：`client.ts` 新增 `classifyApiError()`（`permission` / `not_found` / `network` /
`server`），**只认结构化事实**（`status`），文案留在调用方——同一个 404 在日志页与详情页
的措辞本就不同（#2361 同族，供其复用）。`WifiPage` 据此把 403/401 显示为
「无权限访问 WiFi 资源池（该功能需要管理员权限）。」，网络层失败保留原文案。

## Alternatives

- **#2358 改 `parseIsoToDate` 让 naive 按本地解析**：一行改完，但它是 aware/naive 共用的
  解析原语——会把所有 unaware 字段的展示一起平移，而哪些字段 unaware 是**每列各自的实现
  细节**（本轮只核实了 `users.*`）。风险与收益不成比例，否决。
- **#2358 由后端补时区**（把列改成 `timezone=True`）：要迁移 + 历史数据口径确认，属另一单。
- **#2359 在页面里内联判 `err.status === 403`**：可以，但 #2361 是同族（404 文案），把判据
  收在 `client.ts` 一处，两个页面共用同一分类，避免各写各的 `status` 判断。
- **#2359 顺带做 #2361**：不行——`frontend/src/pages/execution` 当前被另一在窗 Execution
  占用（Registry 前检），按并行纪律不碰。

## Verification

worktree `.wt/stp-2358-2359-frontend`（base `a26cb688`）：

```bash
cd frontend && npx vitest run            # 111 files / 882 passed（另有 1 个 unhandled error）
python scripts/run_gates.py check:quick  # [OK] 10 gates
python scripts/run_gates.py check:pr     # [OK] 19 gates
```

**反例构造**（还原 5 个源码文件到 HEAD 后重跑）→ 4 个测试文件全部失败（`formatNaiveLocalDateTime`
与 `classifyApiError` 不存在 → 导入即红；UserTable/WifiPage 渲染出错误文案）；恢复后 **17 passed**。

新增/修改的守卫：

- `frontend/src/utils/time.test.ts`（新）：naive 值原样输出、与运行环境 TZ 无关、空值占位；
- `frontend/src/pages/users/components/UserTable.test.tsx`（新）：两条时间列渲染出**原样**字符串
  （东八区下若走 UTC 转换会显示成 17:30/20:10，用例会红）；
- `frontend/src/pages/wifi/WifiPage.test.tsx`（新）：403 → 无权限文案**且不出现**「检查后端服务
  连接」；网络层失败保留连接文案（正反对照）；
- `frontend/src/utils/api/client.test.ts`：`classifyApiError` 四类 + 非 `ApiError` 入参归一。

**如实记录**：全量前端套件有 **1 个 unhandled error**，归属用例「取消的安装走 info 提示，不报
失败」——在**主检出（main）上同样复现**（870 passed / 1 error），非本批引入；未在本单修
（属另一 Execution 的安装链面）。

## Revisit

- **全局 naive 契约**：#2358 只修了用户管理页。`users.created_at/last_login` 之外是否还有
  naive 列被按 UTC 展示，需要一次「列类型 × 前端格式化路径」的普查再定统一口径（后端补时区
  或前端按列区分）。本轮只核实了 `users.*` 与两个 aware 反例。
- **`classifyApiError` 的复用**：#2361（Run 日志页 404 文案 + 详情页 not-found 本地化）与
  #2362/#2363 可直接用它；若沿用页面各自映射，应把该函数删掉而不是两套并存。
- **`/wifi` 入口可见性**（#2360）是产品裁决：user 角色是否该看到 WiFi 入口。本单只保证
  「看得到时，失败原因说得对」。
