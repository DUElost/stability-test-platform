# 中兴 V537A 项目 GPU 专项 NFC "Empty tag" 弹窗阻断排障与全量自愈留档

Status: implemented
Class: process

## Decision

针对中兴 V537A / A57（展锐芯片平台 / MyOS 系统，型号 Z2581/Z2582）四专项 7 天循环测试运行期间，12 节点机群（尤其是 172.21.15.68、172.21.15.69、172.21.15.75 等密集摆放节点）在执行 GPU 专项（Plan 116，安兔兔 Lite 3D 压测）时频繁出现 "Empty tag" 弹窗导致压测中断的问题，进行了全链路逆向分析与实机取证排障，并在权威运维手册中留档闭环：

1. **根因定位**：
   - 确认 "Empty tag" 弹窗由 Android 系统原生 NFC 标签解析应用 `com.android.apps.tag/.TagViewer` 弹出；
   - A57 出厂默认开启 NFC 硬件轮询。在机房测试机架上，相邻样机背面紧密贴合或靠近金属支架，各机 NFC 天线持续感应到邻机的射频场或无 NDEF 数据的空载信号，频繁触发 NFC 空标签扫描事件；
   - `TagViewer` 弹窗抢占了顶层 Window 焦点（`mCurrentFocus=Window{... com.android.apps.tag/.TagViewer}`），将正在前台运行 3D 渲染的安兔兔主界面（`UnityPlayerActivity`）切入后台；
   - 安兔兔丢失焦点后挂起 3D 渲染，测试框架因无法在顶层窗口找到 "Start test" 或 "Restart" 控件而抛出 `java.lang.AssertionError: antutu app start test | restart`，导致 GPU 专项中断。
2. **三重持久化自愈治理**：
   - 底层持久化关闭 NFC 硬件轮询：`cmd nfc disable-nfc persist` 与 `svc nfc disable`；
   - 用户态持久化禁用 Tag 组件：`pm disable-user --user 0 com.android.apps.tag`（写入 `/data/system/users/0/package-restrictions.xml`，跨重启永久生效）；
   - 立即释放顶层焦点：`am force-stop com.android.apps.tag`，顶层焦点立即交回底层的安兔兔 3D 压测画面。
3. **文档沉淀**：
   - 更新操作手册 [`docs/operations/zte-v537a-powercycle-apk-debugging.md`](../../operations/zte-v537a-powercycle-apk-debugging.md) §5，建立标准排障与恢复 SOP 以及多主机并发自愈脚本；
   - 在 [`docs/operations/README.md`](../../operations/README.md) 中同步更新手册索引。

涉及文件：
- `docs/operations/zte-v537a-powercycle-apk-debugging.md`
- `docs/operations/README.md`
- `docs/notes/process/2026-10-28-zte-v537a-gpu-nfc-empty-tag-investigation.md`

## Alternatives

- **仅在测试循环中增加 UI 点击取消弹窗（dismiss_dialogs 扩展）**：`TagViewer` 是全局独立 Activity，而非标准 AlertDialog 对话框，没有固定的 "OK/确定" 按钮；单纯靠点击模拟极易误触界面其他元素或漏点，且无法消除 NFC 射频轮询对底层 CPU/电量的持续干扰。直接在硬件和包级别持久化禁用是最彻底、最干净的根本方案。
- **物理增加设备摆放间距或贴屏蔽贴纸**：现场机房机架机位固定且设备总数达 200 余台，物理改造耗时耗力且在巡检维护时容易移位；通过系统命令持久化禁用 NFC 零成本且 100% 可控。
- **修改安兔兔 APK 让其在失去焦点时不暂停渲染**：安兔兔属于第三方商业闭源压测工具，反编译修改 3D 渲染引擎存在兼容性与稳定性风险，破坏测试基准客观性；系统级阻断外部弹窗是标准做法。

## Verification

1. **单机焦点与测试日志回验（以 `172.21.15.75` 设备 `6R0A77LADE9000014` 为例）**：
   - 修复前：`mCurrentFocus=Window{... com.android.apps.tag/.TagViewer}`，测试日志显示 `FAILURES!!! Tests run: 1, Failures: 1, AssertionError: antutu app start test`；
   - 修复后：`mCurrentFocus=Window{... com.antutu.benchmark.full.lite/com.android.module.graphics.marooned.UnityPlayerActivity}`；
   - 本地日志 `/sdcard/Auto/test_log.txt` 验证单轮完整跑满 943 秒，输出 `OK (1 test)`。
2. **集群 12 节点 188 台全量验证**：
   - 运行多主机并发自愈脚本 `scratch/fix_all_nfc_empty_tag.py`，全量 12 节点 188 台设备全部下发成功（0 失败）；
   - 修复前：15.68（10/16 卡在 Tag）、15.69（10/16 卡在 Tag）、15.75（7/17 卡在 Tag）大面积阻断；
   - 修复后全量回查：全网 188 台设备中 `TagViewer` 占用数由 32 台 **降至 0 台**；
   - 安兔兔 3D 压测前台处于正常渲染态的设备达 142 台，其余处于轮次间隙正常重启，压测推进顺畅。
3. **规范遵从**：
   - 操作文档与 SOP 遵循 ADR-0059 写作规范；
   - 运行本地门禁 `check:quick` 验证无代码与格式破坏。

## Revisit

- 当后续设备需要参与真实 NFC 业务功能专项测试时，执行者必须按需调用 `cmd nfc enable-nfc` 与 `pm enable com.android.apps.tag` 恢复其组件状态；
- 后续在平台代码中可将 NFC 抑制命令集成至 `gpu_setup.py` 的 `prepare_device()` 中，作为机群自动初始化的标准环节。
