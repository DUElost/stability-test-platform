# PR-Agent 定位变更：required 门禁 → 非阻塞顾问

Status: implemented
Class: process

## 决定了什么

2026-08-29 起，pr-agent-gate 从分支保护 required checks 摘除（6 项
required），定位改为**非阻塞顾问**：

- PR-Agent 仍自动 review、更新 persistent comment；security concerns
  以红色 check + 评论呈现，**不阻断合入**——合入前人工查看意见即可
- 移除 #421 双保险（gate failure 时 `gh pr merge --disable-auto`）：
  「非阻塞」意味着任何机制（required check 或 disable-auto）都不再
  阻止合入
- 治理检查器 S4 相应移除该锚点（防绕过清单只留 digest pin / fallback
  置空 / 门禁与命令 job 分离 / security 判定），self-test 夹具同步

## 放弃的备选

- 保留 required + fail-closed：AI 审查偶发故障（API 波动）会卡住全部
  合入，人工评审才是本仓的主防线；AI 审查降级为参考意见。
- 保留 #421 disable-auto：gate 红时仍暂停 auto-merge = 半阻塞，
  与「非阻塞顾问」定位矛盾。

## 如何验证

- 分支保护 contexts 实查为 6 项（lint / CodeQL / pr-typecheck /
  pr-compileall / pr-agent-tests / pr-migrate-empty-db）；
- PR 上 gate 照常出 review 评论，红 check 不阻止 auto-merge 合入；
- 治理检查器 --check / --self-test 全绿。

## 何时重议

若 AI 审查质量显著提升或需要自动阻断高危合并（如供应链投毒类），
可恢复 required 或引入「仅 security concerns 阻断」的窄语义门禁。
