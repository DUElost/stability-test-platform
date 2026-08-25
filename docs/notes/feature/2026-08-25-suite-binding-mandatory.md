# mtbf 绑定翻转硬拒（SUITE_BINDING_REQUIRED）+ P2 立项

Status: implemented
Class: feature

## Decision

issue #404 口径的最后一个条件分支落地：`suite_unbound` 观测期翻转硬拒。

**翻转依据**（2026-08-25 复核实测）：部署观测期（08-24~25）`suite_unbound`
告警 **0 命中**；生产唯一引用 mtbf 脚本的 Plan（id=10）已绑定套件；期间全部
4 次 mtbf 派发均为托管 Run——「一个完整运行周期归零」以退化形式满足：
不是有告警后归零，而是根本不存在未绑定流量。

**实现**：观测层 WARNING 移除，preview 与 prepare 双点以
`PlanDispatchError(suite_binding_required=[step_key...])` 硬拒，
端点层经既有 `detail()` 映射为 400 + `{"code":"SUITE_BINDING_REQUIRED",
"mtbf_steps":[...]}`（对齐 `LEGACY_AEE_SCRIPTS_DISABLED` 先例）。
共享判定收在 `plan_dispatcher_core.check_suite_binding_required`。

语义边界：翻转**只针对 mtbf 脚本**——非 mtbf 计划的未绑定派发照常可用
（P0 文件真源模式对其保持开启）；物化器对无 `dispatch_suite` 存量 Run 的
零注入分支保留为防御性代码（测试改为直构裸 PlanRun 覆盖）。无 env 逃生阀，
回滚 = revert（对齐仓库「行为翻转走显式提交而非旋钮」取向）。

## 放弃的备选

- **再等一个日历窗口**：拒绝。零流量下等待不产生新信息；唯一 mtbf Plan 已
  绑定且 D6 冒烟全链验证过托管模式，继续开放未绑定通道只保留误配空间。
- **admission Phase A0 拒绝而非 prepare**：拒绝。prepare 同步拒绝不产生任何
  行、端点直接 400，与 legacy AEE 停用同构；准入层拒绝会先建 QUEUED/PRECHECK
  再失败，制造噪音痕迹。
- **加 env 开关（如 STP_MTBF_REQUIRE_BINDING=0 回退）**：拒绝。行为翻转走
  显式提交，配置旋钮会让「两套基准并存」从 env 层复活。

## 如何验证

- `test_suite_binding_gate.py`：未绑定 mtbf 派发 → `PlanDispatchError` 且
  detail code/mtbf_steps 断言；未绑定**非 mtbf** 计划照常派发（翻转半径
  回归）；绑定派发无告警；两个原「未绑定可达」测试改造为非 mtbf / 直构裸
  PlanRun 形态（冻结零字段与门禁放行分支语义不变）。
- 关联 dispatcher/routes 套件 76 例全绿；backend 全量见 CI；ruff 干净。
- 生产激活：随下次服务重启生效（纯控制面，无迁移）。激活后未绑定 mtbf
  派发在 API 层表现为 400 `SUITE_BINDING_REQUIRED`。

## 边界与何时重议

- 若未来出现「临时想跑一次工具目录现成清单」的诉求，正道是建一个套件并
  import 该清单（CLI 三条命令），而非恢复无门禁通道；
- P2（用例管理页 + `test_case_result` 表与摄入链路 + artifact 白名单扩展）
  已单开 issue 立项，不在 #404 范围。
