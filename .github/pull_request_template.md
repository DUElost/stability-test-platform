# Summary

- 变更内容：
- 动机 / 关联 Issue：

## 测试

- [ ] Agent tests（`python -m pytest backend/agent/tests/ -q`）
- [ ] 前端 type-check / vitest / build（如涉及前端）
- [ ] Backend tests（PG 可用时；跑容器/testcontainers，不连生产库）
- [ ] 根目录 `tests/`（如涉及仓库级契约）

## 文档

- [ ] 涉及协议 / 状态机变更时更新 `docs/design/07-execution-protocol.md`
- [ ] 涉及 env / 部署 / 验收时同步对应文档
- [ ] 新功能按 `PRD/Epic → ADR → design/ → acceptance/` 更新对应验收矩阵（如适用；不适用时说明原因）

## 注意事项

- 不要直接 push main；auto-merge 已开启，不要手动点 Merge。
- 敏感文件（`.env.backend`、`backend/.env`、`hosts.ini` 等）不得进入 diff。
- PR-Agent（DeepSeek v4-flash）自动审查每个 PR / push；security concerns 会阻断合入，其余意见仅参考；需要手动复评时评论 `/review`（仅协作者）。
