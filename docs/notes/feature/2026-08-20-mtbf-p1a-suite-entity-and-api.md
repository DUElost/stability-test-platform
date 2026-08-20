# ADR-0030 P1a：MTBF 套件/用例实体 + 管理面

- 日期：2026-08-20
- 类型：feature
- 上游：[ADR-0030](../../adr/ADR-0030-multi-case-suite-management.md) D1/D6、[P1 设计](../../design/2026-08-mtbf-p1-suite-management.md)（v1.1 定稿）
- 提交：`4a57993`（渲染器 + 指纹）、`1bd079f`（实体 + 端点 + 审计）

## 决定了什么

**1. 渲染器手工产字节，不用 `ET.tostring`。**
导出物要与 P0 已在真机上跑过多轮的输入**逐字节相同**，才能消掉「设备端
OfflineScriptManager 对某种写法敏感」这个没人验过的假设。ET 在四处与生产文件
不同且都不可配置：LF 行尾、属性按 dict 序、`@` 不转义、XML 声明写法。固定
规范属性序 + 显式转义把四项全钉死，130 testpoint / 137 testcase / 76791 字节
的生产快照 parse→render **全等**。

因此 golden 判据从设计预留的「除属性序外逐字节一致」**收紧为零容差**——
P1 设计 §7 #6 的属性序容差不再需要，实施时已消除。

**2. 库漂移检测算出来，不靠端点置空。**
`content_fingerprint` 对库**全量内容**（含 `enabled=false`）算规范化 sha256。
测试断言六条变更路径（增 / 删 / 改名 / 改 times / 停用 / 改 root_config）
全部翻转 `export_stale`，而**没有任何端点清过快照列**。这条断言就是选结构性
检测而非枚举纪律的全部理由：枚举写法下每加一条写路径都要记得清列，评审时
已经漏掉三条。

## 实施期发现的两个坑（都会让导出物失去同构）

**JSONB 重排对象键。** PG 的 JSONB 按「长度优先再字节序」规范化键序：`args`
里 `wifiPWD`(7) 被排到 `wifiName`(8) 前面，导出物随即与源文件不同（实测差异
落在 offset 826）。渲染层的固定属性序救不了它——arg 顺序是**文档顺序**，
既非字典序也非长度序。

修法：`root_config` / `global_params` / `exec_descs` 三个「喂给渲染器」的列
改用 `JSON`（PG `json` 逐字保存原文，含键序）。这三列是不可检索的配置文档，
不需要 JSONB 的索引与操作符，代价为零。附了专门的回归断言，将来若有人为了
加索引改回 JSONB，该断言先于 golden 挂掉并直接指出原因。

**指纹不能算在 ORM 关系缓存上。** 会话配 `expire_on_commit=False`（测试
conftest 即如此）时，提交后已加载的 `suite.cases` 集合不失效，identity map
会把过期集合喂给指纹计算——指纹一旦算在陈旧集合上，「库改了没导出」就漏检。
改为显式按 `ordinal` 查询用例行。生产会话是默认 `expire_on_commit=True`，
这个坑只在测试配置下显形，但代码不该依赖该取值。

## 放弃的备选

- **`ET.tostring` + 语义等价 golden**：省几十行渲染代码，代价是导出物与设备
  面既有输入形态不同，把「OSM 吃不吃 LF / `@@`」变成必须实测的问题。为省
  代码去换一个真机验证轮次不划算。
- **args 存有序 pair 列表**：能绕开 JSONB 键序，但要改 P1 设计 §1.2 的
  `exec_descs` 契约（`args:{}`），影响面比换列类型大。
- **`test_set_attrs` 不入库**：P1 设计 §1.1 的 `global_params` 原本只有
  `sim` + `test_package_ref`。照此实现会让导出的 UiAutomatorTestData.xml
  丢掉 `TestSet` 根属性（含 `TakeScreenshot="true"`）——同样是给设备端换了
  个没见过的文件。已加 `test_set_attrs` 键，**与设计文档的偏离需回写 §1.1**。

## 如何验证

| 项 | 结果 |
|---|---|
| `backend/agent/tests/`（全量） | 1090 过（渲染/指纹 15 条新增在内） |
| `backend/tests/api/test_mtbf_suite_routes.py` | 25 过（testcontainers 隔离 PG） |
| 关联既有套件（project_routes / mtbf_validate） | 50 过，无回归 |
| `alembic upgrade head` | 空库全链路实跑 rc=0，schema 与模型一致 |
| ruff `backend/ tools/ scripts/` + compileall | 零告警 |

golden 断言分两层：服务层 `parse→render` 全等，API 层 `import→export`
**经完整 DB 往返**仍全等——后者才能覆盖列类型这类持久化侧的形变。

## 何时重议

- **P1 设计 §1.1 回写**：`global_params` 补 `test_set_attrs` 键；§7 #6 的
  属性序容差已消除，golden 判据改「逐字节一致」。
- **若要给这三列加 JSONB 索引**：先想清楚导出物不再同构的代价，或改为
  「JSON 存原文 + 另设 JSONB 影子列供检索」。
- **P1b 门禁接线时**：第 3 步用 `content_fingerprint`、第 4 步用磁盘 sha，
  两者已分别有 `exported_content_sha256` / `exported_sha256` 落位；
  `export_stale` 已在列表/详情/`X-Export-Stale` 响应头三处可见。
- **ACTIVE PlanRun 引用守卫**（软删与 export 的 409）留在 P1b——依赖 D2
  绑定字段，P1a 无从判断「谁在引用」。
