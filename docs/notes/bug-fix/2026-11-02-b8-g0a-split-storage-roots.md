# B8 G0-a：拆分日志根与控制面根

Status: implemented
Class: bug-fix

## Decision

按 [#3800](https://github.com/DUElost/stability-test-platform/issues/3800) §3/§4 G0-a、
[切盘方案 v1.2 §4 G0-a](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6572427631)
第 1–6 条实施，Refs #3857。
示例文件的补充 scope 依据 [规划者裁定](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6572827305)。
登记入口的补充 scope 与读写校验契约依据 [方案 v1.3 裁定](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6573386571)。

- `STP_CONTROL_PLANE_STORAGE_ROOT` 覆盖分发内容与 `jobs/`；空/未设置时回落
  `STP_AEE_NFS_ROOT`。两个角色的解析器保持同语义，Agent 不引入控制面依赖。
- `STP_LEGACY_LOG_STORAGE_ROOT` 只由控制面读取，不下发 Agent，因此单独设置它也不改变
  Agent 派生 env。此键只扩充下载校验，未设置不增加候选。DLE 回执校验默认
  不接受旧根；下载显式 opt-in。上传与 retention 的目的地不解析旧根。
- JobArtifact 登记显式以 `writable_family=JOBS_FAMILY` 选择当前根；通用读取调用保持
  当前根与旧根的 allowlist。已知顶层族仍按 `storage_families.py` 归属校验，防止旧日志与
  控制面根同路径时从 `devices/` 等日志族登记为 JobArtifact；不改变既有站点相对前缀布局。
- `storage_families.py` 统一登记 run 族属于日志根、`jobs` 属于控制面根。
  retention 与测量引用此归属表，不自带族到根的清单。`evidence` 留给 G6 登记。
- `agent_env_sync` 的 `STP_NFS_ROOT` 镜像与 PipelineEngine/StepContext 的实际注入共同
  改取控制面根。包源保留 `STP_PACKAGES_ROOT` 显式覆盖，缺省改为控制面根下 `packages/`。
- 来源扫描还发现已发布 MTBF、Sleep、PowerCycle、GPU 脚本以 `STP_AEE_NFS_ROOT` 访问
  控制面配置和结果（如 `mtbf_check/_lib.py:111`、`sleep_check/_lib.py:156`、
  `powercycle_check/_lib.py:172`、`gpu_setup/_lib.py:233`）。因此仅在设置控制面根时，
  脚本子进程的该历史键也注入控制面根；父进程的事件上传、scan、spill 保持日志根。
  脚本族源码和发布登记均未改动。控制面根未设置时，派生 env 逐键保持原值。
- `center-storage-interim-dedup` 登记代码办结，移除硬链接、临时文件清理与数据库查询；
  旧命令入口始终 exit 2，保留说明以接住历史告警链接。此变更不是新的容量止血措施。

来源复核覆盖 `STP_AEE_NFS_ROOT`、`STP_NFS_ROOT`、`resolve_shared_storage_root`、
`get_stp_nfs_root`，并追至 `get_aee_nfs_root`、包源助手和全部校验消费方。
分发/Job 调用点按方案改取控制面根；EventUploader、UploadManager、dedup scan/merge/extract、
DLE 状态转换及健康页继续取日志根；installer 的日志挂载键不改；历史迁移中的脚本参数名不改。
Job/PlanRunArtifact 文件下载经共用路径校验兼容控制面根与旧根，旧 DLE 下载显式接受旧日志根。
DLE 回执的 `_validated_remote_path` 正常分支与 `unassigned` 回落均未开启 `allow_legacy`，
实际只接受当前日志根，因此 `agent_device_log_events.py` 无需修改。`copytree_validated_event_dir`
同样未开启旧根兼容。四个纯读取面（JobArtifact 下载、PlanRunArtifact 下载、DLE 下载、
watcher summary）保持当前根与旧根兼容；DLE 下载是唯一显式开启 `allow_legacy=True` 的调用。
一次性 `jira_extract_run52.py` 与健康页双目标按 #3800 的既定边界保留。

## Alternatives

只修改 hot-update 镜像会被 PipelineEngine 注入覆盖；只改 `STP_NFS_ROOT` 则直接读取
`STP_AEE_NFS_ROOT` 的已发布脚本仍会走错根。子进程兼容避免修改发布单元，父进程与子进程
的根职责在 env 文档中明确标注。没有引入第三种可写根，也没有用旧根作为写入回落。

控制面与 Agent 解析器保留少量同语义副本，以维持 Agent 独立安装边界；控制面镜像模块
保持标准库自足，不因解析根而引入数据库初始化。测量工具新增 `--control-plane-root`，
未设置时与原先 `--center-root` 行为一致。

## Verification

所有 pytest 均经 `scripts/project_python.sh scripts/run_pytest.py`，实际限制为
`memory.max=6 GiB`、`memory.swap.max=0`。控制面测试使用 testcontainers 隔离库；
本次未加载生产 env 或访问生产数据。

已执行的详细命令、最终结果和环境限制见 PR「验证」。反例包括：完整派生 env 对拍、
脚本真实子进程根注入、bootstrap 分别注入日志与控制面根、promote/puller 的实际 jobs 写入、
旧 DLE 下载与回执拒绝、run/job retention 的不同根、旧根与控制面根同路径时旧日志保留、
仅旧根存在时写侧与登记入口拒绝、旧根/控制面同路径及 symlink 回指时登记拒绝、
DLE 正常与 unassigned 回执拒绝旧根、jobs 测量与硬链接工具拒绝。
补充 scope 前的三个 JobArtifact 反例在 `07a03eb8a` 为红，修复后转绿。

独立源码对照探针从 `origin/main` 读取三个 owning files，与当前工作树使用相同临时目录
分别执行；原实现 3 项失败，当前实现 3 项通过（脚本 env 镜像、旧根下载 allowlist、jobs 清理）。
这不是生产或端到端验证。

## Revisit

G0-b 由 Owner 选择窗口并设置新键；合入不表示部署或切盘完成。
在切盘窗口验证脚本包、刷机预检、旧 run 下载和短 PlanRun 上传/scan/merge。
未来脚本族若新增设备日志根用途，复核子进程历史键兼容的适用范围。
旧日志归档/删除仍按 ADR-0062 D12 第 2 条逐 run 取得 Owner 确认。
