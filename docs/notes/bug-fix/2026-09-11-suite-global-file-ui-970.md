# 套件详情页 Global 文件可选导入（#970）

Status: implemented  
Class: bug-fix

## Decision

`TestSuiteDetailPage` 增加「选择 Global 文件」按钮与已选文件名展示；
`globalImportFile` 状态在导入 runtask 时一并传给 `api.suites.import`。
导入成功后清空 Global 选择。

涉及：`frontend/src/pages/suites/TestSuiteDetailPage.tsx`；
测试见 `TestSuiteDetailPage.test.tsx`。

## Alternatives

- 单次多选 file input：浏览器对多文件类型/命名约束弱于两步明示。
- 合并为 zip 上传：超出后端现有 multipart 契约。

## Verification

- `cd frontend && npm run test -- --run src/pages/suites/TestSuiteDetailPage.test.tsx`

## Revisit

若需「一步选双文件」对话框，可在本状态机之上封装，不改后端。
