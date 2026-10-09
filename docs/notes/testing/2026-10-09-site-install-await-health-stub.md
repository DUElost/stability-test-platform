# site-install 单测统一 stub await_health

Status: implemented
Class: testing

## Decision

`tests/test_site_install.py` 的 autouse `_stub_entry_probe` 在既有
`await_frontend` stub 之外，同步 stub `stages.await_health` 为通过。

S4 会对 `http://127.0.0.1:8000/health` 轮询至多 90s；单测不联网，多数用例本就
各自 monkeypatch `await_health`，但 `test_three_component_bundle_passes_s0` 与
`test_legacy_bundle_declaring_retired_host_resources_is_accepted` 漏 stub，
在 CI offline 半程合计烧掉约 180s 墙钟（约 1/3）。与 `await_frontend` 对齐为
共享默认 stub 后，这两例不再睡满超时；负向入口用例仍可在本测内覆盖
`await_frontend=False`（当前无依赖真实 health 失败的负向用例）。

涉及：`tests/test_site_install.py`。

## Alternatives

- 只给那两个用例补 monkeypatch：改动面更小，但与「入口探测已 autouse」不一致，
  仍易漏下一批走到 S4 的用例。
- 缩短生产 `await_health` 超时：会削弱真实安装验收，不采纳。

## Verification

```bash
./scripts/project_python.sh scripts/run_pytest.py \
  tests/test_site_install.py::test_three_component_bundle_passes_s0 \
  tests/test_site_install.py::test_legacy_bundle_declaring_retired_host_resources_is_accepted \
  -q --durations=5
./scripts/project_python.sh scripts/run_pytest.py tests/test_site_install.py -q
```

期望：两慢例合计远低于数秒；整文件仍通过。

## Revisit

若日后增加「health 探测失败必须 FAIL」的负向用例，在该测内显式覆盖
`await_health` 为 False（对照 `test_entry_without_frontend_is_reported`）。
