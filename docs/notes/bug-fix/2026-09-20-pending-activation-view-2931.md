# 脚本版本待激活视图：磁盘 head 版本 vs script 表三态对账（#2931）

Status: implemented
Class: bug-fix

## Decision

给 `check_unreferenced_script_versions.py` 加 `--pending-activation`（复用 #735 的
只读工具，不新造机制），补上「磁盘有、库无行」这一纯盲区。判据形状全部按票面
已踩过的坑设计：

- **每族只看磁盘 head 版本**（`version_key` 数值序，`1.0.10 > 1.0.9` 有测试钉）：
  owner 第一版用「模板/seed 是否引用」做可达性判据跑出 135/135 全不可达的假
  结论（模板全线 pin `1.0.0`，真引用面在 DB `plan_step`）——教训落地为「判据在
  目录行与数据行上」+「旧版零引用不进本账（那是 #735 退役面，且会把真滞后淹掉）」；
- 三态 `unregistered / inactive / (不列出)=已生效`：视图输出即落后集合，
  空 = 全部生效，与 `script-versioning.md` 新收尾判据同构（「合入后该视图不再
  列出它」）；
- **账本非门禁**：默认退出码 0 表「对账完成」，落后项不判红（第 3、4 道是部署
  SOP 动作，判红会把工具变成部署强依赖）；但 `STP_SCRIPT_ROOT` 未设 → exit 2
  「无从判定」——与 #735 guard 的 UNKNOWN 语义同族，空环境不得伪装成「没有落后」。

复用同一次 DB 连接与 `script` 表查询（rows 已在手），磁盘侧 `scan_disk_script_versions`
与状态判定 `pending_activation_view` 均为纯函数（tmp_path 单测零 DB）。

## Alternatives

- **独立新工具脚本**：弃——与退役巡检共享 DB 行/版本排序/词表，拆开两处漂移；
- **做成 CI 门禁**：弃——scan 是部署动作，CI 里没有部署树（STP_SCRIPT_ROOT 语义
  即部署侧），把 SOP 缺口伪装成代码缺口会恒 SKIP 假绿（#2866 教训同款形态）。

## Verification

- 新单测 4（目录枚举过滤/三态/head-only/数值序/名字过滤）全绿，
  `test_script_retirement_guard.py` 同文件族 23+4 passed；
- **生产实证**（只读，主检出部署树 script root + 生产库）：票立案 5 项中
  `gpu_finish 1.0.6`/`ensure_root 1.0.1` 已消失（中间 scan 注册过），同时**新捞出
  3 个立案后才合入的滞后项**——`clear_recents v1.0.2`（#2936 当日合入）、
  `monkey_setup v2.3.9`、`powercycle_setup v1.2.1` 均 `unregistered`：视图对
  「scan 之后继续合入」的增量漂移即刻可见，正是本单要的敏感性；
- ruff/compileall/`check:quick` → 见 PR。

## Revisit

- 第 4 道（plan_step 重指）的「谁该重指」仍靠人（#2865/#2926 走的是仓库侧 pin）；
  若 DB 侧历史 run 的重指需求出现，先判「引用 head 的 plan_step 清单」——数据行
  对账，勿回到散字符串；
- 本视图与 fleet 部署树一致性（#2386 的树窗口问题）叠加时：scan 未跑与树不对
  都表现为 unregistered，第一版不区分（都是「该跑一次 scan 的信号」）。
