# 设备 product 采集与 NX3Carrier 展示

Status: implemented
Class: feature

## Decision

保留 ADB `product` 原值，与 `model`、项目归属和在线状态分开。
本次只交付身份采集与展示，不提供刷机兼容授权。

- `device_discovery.py` 解析 `adb devices -l` 的 product，`heartbeat_thread.py`
  直接透传发现结果；shell 失败不会丢掉 product，也不会把离线改为在线。
- `device.product` 为可空 `varchar(128)`；迁移 `9b7e24a6d081` 仅新增列，无回填、无索引。
  心跳接受有效非空字符串，缺失、空白或无效值不覆盖已有观测值；真实的新值可更新旧值。
  旧 Agent 可以继续上报；已有值在未收到新观测时只是最近一次已知值。
- 设备列表/详情响应携带 product，列表 API 支持精确 product 筛选且分页 total 同口径；
  前端类型、分页客户端和查询键同步扩展。人工创建设备不填写自动观测字段。
- 设备页及执行选机表格显示原始 product；矩阵 tooltip 显示同一内容。
  设备页和执行选机页支持 product 筛选及易读名称搜索；执行页 URL、草稿和条件清除同步。
  两处 CSV 导出包含原始 product，继续使用原有公式与结构转义。
- 同一前端格式化函数只对已确认组合显示 NX2、NX3 或 NX3Carrier；
  `sys_mssi_64_cn_armv82` 单独不足以判 Carrier，需同时是 MLD_NX3。
  缺失值显示“未上报”，不添加 variant 持久字段，不改型号或项目映射。

## Alternatives

- 只打标签：人工维护易漂移，无法反映设备实时上报的 product。
- 把型号改为 NX3Carrier：破坏原始型号与现有项目成员映射。
- 根据型号回填 product：普通 NX3 与 Carrier 型号相同，不能推断。
- 本次顺带修改刷机路由：目录对应关系及属性稳定性尚未确认，留待独立需求。

## Verification

- Agent 相关测试：52 passed，包含五台样本与 shell closed 的心跳透传。
- 后端设备/心跳相关测试：61 passed，包含同型号不同 product、分页筛选、权限、
  旧 Agent、缺失/无效值保留、新观测更新。
- 前端相关测试：140 passed，包含 Carrier 展示/搜索、同型号选机筛选、URL 恢复和 CSV。
- 响应契约与 product 布局静态检查：17 passed。
- `check:quick`：16 gates passed；未配置运行时数据库，schema-at-head 显式 WARN/跳过。
- `check_pr_migrate.py`：隔离 postgres:16 空库升级、ORM schema 比对和 seed 身份验证通过；
  5 项存量 schema diff，新增 0。未在生产库执行迁移。
- 当前会话没有可用浏览器；交互由 Vitest 验证，长文本限制由静态布局检查验证，
  未声称通过真实浏览器或真机验收。
- 完整 Agent 套件（`scripts/run_pytest.py backend/agent/tests/ -q`）：2571 passed。
- 完整 API 套件（`scripts/run_pytest.py backend/tests/api/ -q`，隔离 testcontainers）：
  1400 passed。客户端与筛选补验：12 passed；最终类型检查和内存受限的前端构建通过。
- 代码提交 `a2822f0f4` 的六项 required checks 全部通过；本次后续提交仅更新验证记录。

## Revisit

- 生效顺序：先部署控制面迁移/API，再更新 Agent 代码，最后核对页面和实际心跳。
  只合入代码不会补齐生产数据；本次没有执行部署、生产迁移或 Agent 更新。
- 防互刷是第二步：确认 NX2/NX3 包对应关系和 product 稳定性，再为新刷机脚本版本
  增加实时身份与 manifest 组合校验；显式目录也不能绕过校验。
- 设备 shell closed 的可用性问题独立处理；本变更只保证可读身份继续展示。
