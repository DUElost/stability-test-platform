# #2643 关单：站点告警分层 + 抓取面对拍已落地

Status: implemented
Class: bug-fix

## Decision

关闭 [#2643](https://github.com/DUElost/stability-test-platform/issues/2643)。票面三项建议
均已收口：

| 建议 | 落地 |
|---|---|
| 1（站点只装可见面） | #2731 → `deploy/prometheus/site-alerts.yml` + installer 渲染 |
| 2（站点补控制面 scrape） | **未选**（与方向 1 互斥） |
| 3（机械对拍） | #2717 → 站点抓取面/生产者棘轮常驻 `pr-agent-tests` |

## Alternatives

- **保持 OPEN 等现场升级回执**：弃——仓库模板层目标已达成；现场 `/etc` 手改面另议。

## Verification

- `#2717` / `#2731` MERGED；`tools/site_config/stages.py` 标注方向 1
- 关单前最后「等方向裁决」评论之后方向已落地，本单补关

## Revisit

无。若站点以后要兼抓控制面 `/metrics`，另开单并过 ADR-0024 网络面。
