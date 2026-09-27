# monkey_setup v2.3.13：remote_dir / install.pkg_name / wifi 参数注入收口（#3173 / B1-G4-2）

Status: implemented
Class: bug-fix

关联：[#3173](https://github.com/DUElost/stability-test-platform/issues/3173)（本单）、
[#3463](https://github.com/DUElost/stability-test-platform/issues/3463)（批次 B1 规划方案
v1.2 §9.2 G4-2 / §9.1 裁定）、[#3472](https://github.com/DUElost/stability-test-platform/pull/3472)
（G4 首轮 PR，复核发现本单三处）、connect_wifi #816（shlex.quote 参照实现）。

## Decision

按 #3463 v1.2 §9.2 G4-2 执行，不重新设计。背景：#3472 复核（PR 评论 2026-09-27T10:42Z）
发现三处 F3 漏面——v1.0 的 F3 扫描按 `rm/dd/chmod/pm/setprop` 关键字进行，漏掉了
`mkdir/cat/cd/tar/dumpsys/cmd wifi`；规划者裁定纳入（§9.1），并细化 `remote_dir` 复用
同文件已有的 `validated_remote_path`（与 `push.files[].remote` 判据一致，覆盖 §9.2 原文的
`/sdcard/` 或 `/data/local/tmp/`）。三处修复（只改 `monkey_setup.py`，复用文件内校验函数）：

1. **`push.remote_dir`**（原 :295）：cfg 入口经 `validated_remote_path()` 校验后再
   `.rstrip("/")`——判据 `^/(?:data|sdcard)/[A-Za-z0-9._/-]{1,120}$` 且拒绝 `..` 段；
   校验位于 `cat {marker}` / `mkdir -p` / `cd ... && tar` / adb push **全部**使用点之前，
   非法值整步转红。空串同样是配置错误（不回退默认值）；默认 `/sdcard/test_resources`
   通过。
2. **`install.pkg_name`**（原 :415）：cfg 入口 `validated_pkg_name(pkg_name,
   "install.pkg_name")` 校验一次，之后 `dumpsys package` 与结果消息等所有使用点只用
   校验后的值；非法值整步转红，不进入 shell。
3. **`wifi.ssid/password`**（原 :203）：port connect_wifi #816 做法——
   `f"{shlex.quote(ssid)} wpa2 {shlex.quote(password)}"`，按「单个 shell 参数」语义
   转义；**不加字符白名单**（SSID 允许任意字符）。「已连接」判断的 `ssid in status`
   子串逻辑保持不变。

配套登记：`--register monkey_setup 2.3.13`（manifest append-only，sha `5a120f9fda17`，
2.3.13 未被占用）；`monkey.json` / `monkey_watcher_patrol.json` 的 `script:monkey_setup`
pin 2.3.12 → **2.3.13**（#2865 pin 守卫判据 pin==head，无需 EXCEPTIONS）。

## Alternatives

- **`remote_dir` 用「/sdcard/ 或 /data/local/tmp/」独立白名单**：§9.2 原文措辞；规划者已
  细化为复用 `validated_remote_path`（`/data/` 前缀超集），避免同一文件出现两个相近但
  不一致的路径判据——采纳细化版。
- **`wifi.ssid/password` 套标识符白名单或拒绝 `$()`/引号**：SSID 合法字符面比包名宽
  （复核明确「SSID 允许任意字符」），白名单会拒绝现网真实 WiFi 名；按参数语义 quote
  才是正确形态（connect_wifi v1.0.1 同款先例）。
- **顺带修 `step_root`/`step_install` 体内直连 `subprocess.run(text=True)`（F2 残留）**：
  §9.2 未授权，F2 的族面边界维持 G4 首轮裁定（helper 层），不顺手扩面。

## Verification

- `python -m pytest backend/agent/tests/test_monkey_setup_3463_g4_2.py -q` → **19 passed**
  （remote_dir 5 反例整步转红且 `adb_shell`/`_push_or_timeout` 零调用 + 默认值/自定义合法值
  通过且 marker 路径精确；pkg_name 2 反例零 subprocess + 合法值 dumpsys 命令串精确；
  wifi `$()`/反引号/单双引号/`;` 经 `shlex.split` 逆解析还原原始值 + 字面引号内断言 +
  已连接分支不发起连接；SourceGuard 三锚点「旧原形 assert_absent + 新形态 assert_present」）。
- **变异验证（修复前原形上，三类测试各至少一条红）**：
  - remote_dir 回退 `cfg.get(...).rstrip("/")` 原形 → **6 failed**（5 个非法值 step 级
    反例 + SourceGuard push 守卫）；
  - pkg_name 去掉 cfg 入口校验 → **3 failed**（`a;reboot` / `$(id)` + SourceGuard
    install 守卫）；
  - wifi 回退双引号直插原形 → **3 failed**（shlex 逆解析 + 字面断言 + SourceGuard
    wifi 守卫）；
  恢复修复后 19 passed。变异用原形临时改写源树执行，未留痕（已 `git checkout` 级恢复并复跑）。
- `python tools/dev/check_script_packages.py` → 绿（35 个族树与最新登记等价）。
- `python tools/dev/check_tool_manifest.py --base origin/main` → 绿（40 族 / 232 条目，
  append-only）。
- `python -m pytest backend/agent/tests/ -q -k "monkey_setup"` → **104 passed, 2308
  deselected**。
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (16 gates)`。
- `python -m pytest tests/ -q` → 见 PR 正文（首次 1 failed＝`test_payload_root_clean_3112`
  抓到新测试文件未跟踪——该门禁的正当拦截，`git add` 后复跑通过；提交后全量重跑绿）。
- G4 首轮的 `test_template_pins_monkey_setup_2312`（精确值 2.3.12）随本单顺延更新为
  `..._2313`（注释记录两轮直升语义）；其余 G4 测试未动。

## Revisit

- **生效未做**：按 #3463 §5（v1.2：激活前 §9 全部追加单元复核通过）由 Owner 窗口统一
  部署 + publish + scan + 重指；重指目标 = §5 第 4 步列的 monkey_setup **2.3.13**。
- **F3 扫描方法教训**：v1.0 按「危险命令关键字」扫描漏掉 `mkdir/cat/cd/tar/dumpsys/cmd`
  五个入口；§9.1 已改按「所有 f-string 设备 shell 调用 × 插值来源追溯到 cfg/params」重扫，
  本单三处即该重扫的产出。后续新族改动建议沿用后一种扫描面。
- **helper 副本模型**：`validated_*` 现有六族各持副本（#3463 §2 A/B/C 待 Owner 裁决）；
  若走 A（机械守卫），本单未新增跨族分歧（判据与 G4 首轮、clear_recents 完全一致）。
