# ADR-0040 v1.2：D8 host-resources 层退役（2026-09-26 owner 裁决采纳）

Status: implemented
Class: architecture

## Decision

起草 ADR-0040 **D8**：退役 D1 的 `host-resources` artifact 及其收敛层——内容（flashtool / AIMonkey）已由 ADR-0051 D7
工具包承接并于 2026-09-26 激活（canary 真机 run 575/576、15 计划重指、48/48 预热、全机队回归 476 次成功；旧版本 retired #3431），
本层已无消费方。部署单元回到 `agent-code` 单 artifact；工具身份 = 消费时按包 sha 实测核验（强于本层自报 digest，#3128）。

分四步、每步可无损回滚（`resources/` protect-only 保留、不清理主机）：R1 控制面停判停推 → R2 Ansible 撤资源推送与 #2166 断言 →
R3 release manifest 去分量（解除 ADR-0051 D8 结构替代 `check-deploy-source` 的前置）→ R4 Agent 心跳 / wrapper 子命令（ADR-0037 同 PR 回填）/
DB 列与前端。本层登记进过渡台账（`host-resources-layer`，exit = ADR-0040#D8，due 2026-12-31）。

方向级修订：先以 **draft PR** 提交、ADR 内如实标「待 owner 裁决」；2026-09-26 owner 裁决**采纳 D8 与 R1–R4 顺序**（未作调整），
替换为裁决日期后转 ready。本 PR 只写入决策；R1–R4 的代码与运维动作各开实施 PR。

## Alternatives

- **保留本层作第二下发通道**：同一批工具两个身份（自报 digest vs 包 sha）并存，违背 §10 判据唯一性；弃。
- **退役与主机清理一并做**：清理不可逆、且与退役无依赖；解耦才能每步无损回滚；弃（清理另行裁决）。
- **直接实施不先修 ADR**：约 28 个非测试文件、五层（Agent 心跳 / 控制面 DB 列与资源层 / Ansible / 发布与站点安装 / 前端告警），
  改的是 ADR-0040 的部署单元定义，属方向级——先裁决。

## Verification

- 文档修订；`check_transitions` 9 条 / 5 在途（新条目 exit 锚解析通过）；`check:quick` 16 gates OK
- 事实引用均可回溯：激活证据见 #3288 评论（canary / 重指 / 预热 / 回归），旧版本退役见 #3431，自报 digest 短板见 #3128

## Revisit

- R1–R4 各开实施 PR（跟踪 #3288），按依赖顺序；R4 触发 ADR-0037 §7-5 联动回填，R4 完成即结项台账 `host-resources-layer`。
- 若实施中途暂停：台账条目 due 同步调整或转 dropped（附理由），不留无主过渡。
