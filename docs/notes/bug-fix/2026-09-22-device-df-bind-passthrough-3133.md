# #3133 设备 df /data bind 穿透形态解析落空（Z2581 存储覆盖率 44% 根因）

Status: implemented
Class: bug-fix

## Decision

`parse_df_data` 的数据行判据从「行含 `/data` 字样」扩为「行含 `/data` **或**
行以 `/dev` 开头（strip 后）」。第四种真机形态（ZTE Z2581/Z2582、部分 MLD）：
`adb shell df /data` 解析到 bind/穿透挂载，Mounted on 列显示规范挂载点
`/mnt/pass_through/0/emulated`，整行无 `/data` 字样——旧判据落空返回
`(None, None)`，`disk_total/disk_used` 恒 NULL（fleet 在线 Z2581 覆盖仅 44%、
Z2582 18%，其余机型 99–100%）。

取 `/dev` 前缀而非「跳过表头取任意行」：`df` 带路径参数只回该文件系统一行，
首个 `/dev` 行即目标行，无歧义；表头（`Filesystem …`）与错误行（`df: /data: No
such …`）天然不以 `/dev` 开头——后者仍走原「含 /data」路径进单行分支、解析不出
返回 None，既有「保守不猜」行为面不扩大。真机两形态（.56/Z2581、
.100/MLD_LX3 原始输出）作为 fixture 锁进回归测试。

## Alternatives

- **改采集命令为 `df /data/user/0` 或 `storagemgr` 查询**：绕开形态问题但引入
  路径不存在的新失败面，且已部署的 `df /data` 口径在 444+ 台设备上工作正常；
  解析侧补形态是最小变更。拒绝。
- **行过滤直接改成「取最后一行」**：`df` 异常退出时 stderr 混排会取错行；
  `/dev` 判据更精确。拒绝。
- **把「老 agent 主机 12 台（digest db6c1）」并进本单**：那是部署状态不是代码
  缺陷；处置＝本 PR 合入后走 control-plane-deploy §3 一次全 fleet 热更新，
  顺带收口（见 #3133 回执）。拒绝混装。

## Verification

- `pytest backend/agent/tests` → **2273 passed**（含新 `test_parse_df_data_bind_passthrough_form`
  真机 fixture；既有三形态与垃圾输出用例全保留原断言）。
- `python scripts/run_gates.py check:quick` → 14 门禁全绿。
- 部署后收口（待执行，合入 → §3 canary→批量热更新后查生产库）：按机型覆盖率
  复查，预期 Z2581/Z2582 → ≈100%（残差＝窗口内新接入/unauthorized/offline）；
  同时 digest 普查 db6c1 应清零。未部署前本修复不生效（合入 ≠ 生效）。

## Revisit

- 若热更新后 ZTE 残差仍高，下一个形态假设是 `df` 输出带 `-T` 类型列或本地化
  表头——真机取证路径同本单（SSH `adb shell df /data`）。
- 覆盖率指标当前只能靠页面/库查询人工看；若要常态化，走 #2754/#2902 家族在
  metrics 面加 `device_disk_coverage` gauge（需 ADR 级消费者裁决，勿顺手）。
