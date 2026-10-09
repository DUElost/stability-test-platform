# ip-leak：放行 package-lock.json（SERIAL_LIKE 假阳性）

Status: implemented
Class: bug-fix

## Decision

将 `package-lock.json` 加入 `tools/dev/check-internal-ip-leak.py` 的
`ALLOWLIST_SUFFIXES`（basename 后缀匹配，覆盖 `frontend/package-lock.json`
等路径）。npm lockfile 的 `integrity` 等字段是不透明 Base64；`SERIAL_LIKE`
会把其中 12–24 位大写字母数字混合片段误判为 Android 设备序列号，阻塞
required CI `lint`（PR #3620 dependabot frontend-patch-minor）。

按 basename 放行而非逐 token 进 `SAFE_TOKENS`：lockfile 随依赖 bump 持续
再生，逐 token 会无限膨胀且无法覆盖下一次假阳性。

## Alternatives

- **SAFE_TOKENS 放行本轮命中的 token**：能最快转绿，但下次 integrity 变化仍会红；
  与「按需扩充、每条须附理由」的 token 白名单定位不符。放弃。
- **仅 `ALLOWLIST_FILES` 写死 `frontend/package-lock.json`**：功能等价于当前
  仓库布局，但若日后出现其它路径的同名 lockfile 仍会假阳性；suffix 更稳。
- **从 SCAN_SUFFIXES 去掉 `.json`**：误伤会真正承载资产字面量的 JSON 配置/夹具。
  放弃。
- **改 SERIAL_LIKE 规则收窄形态**：影响全仓真实 serial 检出面，超出本 PR 止血范围。
  放弃；若假阳性扩散到其它生成物再议。

## Verification

- `python3 tools/dev/check-internal-ip-leak.py --check` → 通过（含
  `frontend/package-lock.json`）。
- `python3 tools/dev/check-internal-ip-leak.py --self-test` → 全绿（含 lockfile
  integrity 尾段样例期望命中数 0）。

## Revisit

若仓库出现其它会持续再生 SERIAL_LIKE 假阳性的生成物（如其它包管理器
lockfile），优先按 basename/路径进 `ALLOWLIST_SUFFIXES` / `ALLOWLIST_FILES`
并注明「不透明生成内容」，仍避免把生成物 token 堆进 `SAFE_TOKENS`。
