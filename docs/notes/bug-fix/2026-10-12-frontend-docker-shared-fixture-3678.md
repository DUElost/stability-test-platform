# 前端 Docker 构建的共享夹具输入（#3678）

Status: implemented
Class: bug-fix

## Decision

承接 [backstop #3678](https://github.com/DUElost/stability-test-platform/issues/3678)
的前端 Docker 构建失败。main 基线为 `cba2cf90857bc4b6dc5471f4eb5bd58c267bca49`。
这是 [B5 #3653](https://github.com/DUElost/stability-test-platform/issues/3653) U1 引入的共享夹具
构建输入缺口，与已通过复核并合入的 [#3679/#3684](https://github.com/DUElost/stability-test-platform/pull/3684)
告警守卫修复分开处理。

`npm run build` 的 TypeScript 检查包含前端测试；
`draftParameterMerge.test.ts` 引用仓库级 `tests/fixtures/plan_parameter_merge.json`。
镜像构建把 frontend 放在 `/app`，该相对引用解析为 `/tests/fixtures/plan_parameter_merge.json`。
原 Dockerfile 只复制 frontend，未提供这个输入。

仅在 build stage 补一条 COPY，把同一共享 JSON 放到该路径。
TypeScript 检查范围、前后端共用夹具、npm 脚本与运行镜像的 dist-only 拷贝规则保持原样。
没有另造夹具副本或改变共享浅合并语义；运行阶段不带此测试输入。

## Alternatives

- 排除测试类型检查：会改变既有检查范围，修复范围超过构建输入缺口。
- 将夹具复制到前端源码：会产生第二份事实源，偏离 B5 的共享夹具约定。
- 改整个镜像目录布局：会连带修改 WORKDIR 与运行阶段产物路径，当前无需这样做。

## Verification

项目 Python 与 Node 依赖按 local-development 在独立 worktree 初始化，不加载生产配置。
Docker 验证使用本单独立 docker-container builder；实际 cgroup 为
`memory.max=6442450944`、`memory.swap.max=0`。
builder 的网络/代理仅用于本机测试，不修改生产 Docker 配置。

- `npm --prefix frontend run test -- --run src/components/parameters/draftParameterMerge.test.ts`：
  1 文件、7 passed，exit 0。
- `env -u DATABASE_URL -u TEST_DATABASE_URL ./scripts/project_python.sh scripts/run_gates.py check:quick`：
  16 gates OK，exit 0；schema-at-head 无配置，按既有行为跳过。
- 保存 main 原 Dockerfile 为 `/tmp/3678-Dockerfile.frontend-baseline`，实际完整镜像构建
  exit 1，`npm run build` 报同一 `draftParameterMerge.test.ts(4,21)` TS2307，构建步骤 exit 2。
- 修复后的同一完整镜像构建 exit 0（包括 TypeScript、Vite、nginx 运行阶段与镜像加载）。
  本地镜像 `stp-test-3678-frontend:fixed` 的 ID 为
  `sha256:66e1a8ad6258ee52b19cf299712204fe583220e46cd79c6a9f4cbb74b23f6f18`。
- 运行镜像在禁网、128 MiB、swap 0 的一次性容器中检查：index.html 与 assets 存在，
  `/tests/fixtures/plan_parameter_merge.json` 不存在，exit 0；没有启动前端服务。
- 本地测试依赖软链在 Docker 对照期间移除，使输入与 CI 的干净检出一致，随后恢复。
  首次网络连接失败和软链上下文失败均不计作语义红灯；上述基线红与修复绿才是构建对照。
- 构建使用现有网络代理参数，未把代理值写入仓库：

```bash
docker buildx build --builder stp-3678-fixture --progress plain --load \
  --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
  -f /tmp/3678-Dockerfile.frontend-baseline -t stp-test-3678-frontend:baseline .
docker buildx build --builder stp-3678-fixture --progress plain --load \
  --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
  -f Dockerfile.frontend -t stp-test-3678-frontend:fixed .
```

日志在 `/tmp/3678-docker-baseline-final.log`、`/tmp/3678-docker-fixed.log`；
当前 PR head 的 required CI 由 PR 正文记录，本地构建不替代 required CI。

## Revisit

此处修复的是当前共享夹具的构建输入；新增仓库外层构建依赖时，需要同步检查镜像上下文。
本次没有部署或替换生产镜像。合入后仍需 main/backstop 全绿，#3678 由既有工作流自动恢复，
再按原条件引用恢复证据关闭 #3679。构建通过不替代 B5 各单元的独立验收或整批激活。
