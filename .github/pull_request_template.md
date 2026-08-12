## Summary

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

## 注意事项

- 不要直接 push main；auto-merge 已开启，不要手动点 Merge。
- 敏感文件（`.env.backend`、`backend/.env`、`hosts.ini` 等）不得进入 diff。
- CodeRabbit 是 best-effort 参考；需要它对当前 head 复评时评论 `@coderabbitai review`。
