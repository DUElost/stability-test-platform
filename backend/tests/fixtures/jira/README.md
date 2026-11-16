# Jira 提单联调夹具

- `transsion_upload_list_real.xlsx` / `tinno_upload_list_real.xlsx`：由工具仓
  `DUElost/automation-toolkit` 返修候选 `12587942f6058621b732e3154c139eb57b9c8eb4`（待 #27 独立复核）
  的真实阶段一脚本生成，保留真实列布局（含 `Signature ID` / `Occurrence IDs` /
  `Occurrence Keys` / `Occurrence Count` / `Attribution Status` 等归因列）。
- 生成输入：3 行去重 `.xls`（签名 101/102/103，发生 1001–1006）与同范围
  `stp.jira_delivery_input/v1` manifest；`Reporter` 固定为 `example_reporter`。
- **固定模拟访问段**：生成入口把两套工具的 `build_log_server_access_text()` 替换为
  固定文本（`example.invalid` / `example_user` / `<simulated>`），其余字段、列顺序、
  样式和归因列由真实工具生成，未手工重建工作簿。
- **发布前检查**：逐单元格扫描地址/账号/口令与 URL/邮箱模式；检查隐藏工作表/行/列、
  AutoFilter、超链接、批注、定义名称、外部链接与 `docProps`（creator 仅 `openpyxl`），
  结果为未命中。测试只把身份列改写为当次数据库登记值并重排/筛选，不重建列结构；
  不连接真实 Jira，未使用真实凭据。
