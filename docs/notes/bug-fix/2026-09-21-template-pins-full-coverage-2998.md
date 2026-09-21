# 模板 pin 全族追新 + 两界不一致的例外机制（#2998）

Status: implemented
Class: bug-fix

## Decision

**先裁决考古，再扩面**——票面要求「先裁定哪几族故意钉旧」，裁定用的事实取自 git：
16 族 pin 全部创建于各自模板引入时（gpu 三件套=2026-08-31 `df9305f1` 等），
**没有任何一次 commit/文档/issue 记录过"故意钉旧某稳定版"**；模板上最近的
pin 变更恰是 #2865 往最新版追的两笔。"全追最新会误伤故意钉旧"是假想敌——
真问题是"没有可核查的例外通道"。据此：

- 23 处 pin 一次追新（8 模板文件，含 ddr/mtbf/standby 的 ensure_root 顺手跟上）；
- 守卫名单两族 → **模板内全部 16 族**，另加 `test_template_script_actions_all_in_guard_list`
  防"新脚本族进模板漏进名单"（漏名单=漏守卫，本批把入口焊死）；
- **EXCEPTIONS 机制**承接真例外：`(版本, 理由+删除条件)` 二元组，理由须含 issue 引用；
  自证测试断言 豁免版本≠磁盘 head（**head 追上后守卫拒绝保留该例外**——防"临时"
  沉淀成永久，AGENTS 过渡态纪律的守卫化）且 ≤ head（不许钉出磁盘没有的高版本）。

## 唯一在账例外：gpu_setup 钉 1.2.1 非磁盘 1.2.2

#2998 暴露的**规则级真相**（票面没写到）：pin 的上界不是磁盘而是 **script 表
registered∧active head**——prepare `_validate_script_refs` 按库校验，把未注册的
磁盘版 pin 进模板 = 该模板新建 Plan 全 422。`--pending-activation`（#2931 昨日
落地）当场抓到 `gpu_setup v1.2.2 未注册`：先跑它、后钉模板，两工具形成闭环
（#2931 的账本第一次被 #2998 消费，当日互证）。`script-versioning.md` 步骤 2
同步改写为「已注册且激活的最新版」语义。

## Alternatives

- **全族一刀切钉磁盘 head**：弃——gpu_setup v1.2.2 会立刻把 GPU 模板打成
  新建 422（守卫绿着生产炸，正是 #2998 反对的"两陈述不能同时成立"的解法：
  统一上界改为"已注册最新版"而非偏袒任一侧）；
- **只扩名单不加 EXCEPTIONS**：弃——票面"故意钉旧"担忧若未来成真，无出口就会
  有人整族退出守卫；有出口的例外才会被使用；
- **等 scan 注册 v1.2.2 再一批做**：弃——15 族 pin 追新不依赖它，把个例外
  变成全量拖延正是 #2998 批评的"合入了但不生效"模式。

## Verification

- 守卫 4 例（主对拍/名单无空项/模板全覆盖反证/例外三断言）+ `test_plans_api`
  72 + plan_runs_api/read_api_auth + #2055 迁移重放 → 见下条 run 输出与 PR；
- diff 形态自证：8 文件 23+/23-（纯 version 值替换，JSON 格式零扰动）。

## Revisit

- 下次部署 scan 后：`--pending-activation` 清空 → 删 `script:gpu_setup` 例外 +
  pin 1.2.2（一条 commit，本 note 是其工单）；
- #735 零引用视图会因这批追新把大量 1.0.0 旧版推成零引用——那是退役面正常的
  进账，由 #735 guard 的冷却期管，不回头钉旧。
