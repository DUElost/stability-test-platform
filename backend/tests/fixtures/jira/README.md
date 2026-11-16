# Jira 提单联调夹具

- `transsion_upload_list_real.xlsx` / `tinno_upload_list_real.xlsx`：由工具仓
  `DUElost/automation-toolkit` 提交 `24f86c895740ebf735533b1914047118fd6544de`
  的真实阶段一脚本生成，保留真实列布局（含 `Signature ID` / `Occurrence IDs` /
  `Occurrence Keys` / `Occurrence Count` / `Attribution Status` 等归因列）。
- 生成输入：3 行去重 `.xls`（签名 101/102/103，发生 1001–1006）与同范围
  `stp.jira_delivery_input/v1` manifest；命令分别为：
  - `python generate_transsion_jira_upload_list.py --add-main-excel input.xls ... --output-dir <ws> --attribution-manifest <ws>/manifest.json`
  - `python generate_tinno_jira_upload_list.py --add-main-excel input.xls ... --output-dir <ws> --attribution-manifest <ws>/manifest.json`
- 测试只把身份列改写为当次数据库登记值并重排/筛选，不重建列结构；不连接真实
  Jira，未使用真实凭据。
