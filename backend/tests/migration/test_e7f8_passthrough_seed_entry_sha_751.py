"""#751 — e7f8 seed content_sha256 必须是入口脚本（非 _lib.py）。

ADR-0051 Phase 3 删除了 ``backend/agent/scripts/<name>/v<ver>/`` 版本目录，本用例原先
「现场读文件算 sha」的事实源随之消失（#3247：夜间全量 CI 因此确定性红）。版本目录内容
不可变（ADR-0039 D2/D3），且已原样封进发布包，所以把入口脚本 sha 与 ``LIB_SHAS`` 一样
**钉成常量**。出处（2026-09-25 三源逐字节对拍一致）：

- git：``64d0ef93^:backend/agent/scripts/<name>/v<ver>/{<name>.py,_lib.py}``（Phase 3 删目录前一刻）；
- 站点发布包：``packages/<name>/<ver>.tar.gz`` 解包后的同名文件，整包 sha 等于
  ``tool_manifest.json`` 登记的 ``package_sha256``；
- 迁移 seed 本身的 ``VERSIONS[*].sha``。

仓内仍可复核的一条是 ``tool_manifest.json`` 的 ``script`` 字段：它把入口登记为
``<name>.py``——seed 记的必须是这个文件，而不是同包里的 ``_lib.py``。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
SEED = (
    BACKEND
    / "alembic/versions/e7f8a9b0c1d2_seed_gpu_power_sleep_resources_passthrough.py"
)
ENTRY_SHAS = {
    ("gpu_setup", "1.0.2"):
        "a654a624a197dcbdfa626dbfd478114273a19253da779ff03fbae13f540748b1",
    ("powercycle_setup", "1.0.1"):
        "29136c9ce24f9dfcc90ad705988f9a5aa153de5a59d96e697ea278e59b9e3d7e",
    ("sleep_setup", "1.0.1"):
        "970a02133edcf75528adb74cdcf413d89f8d3a4f6f384ab3380d1c872db6db79",
}
LIB_SHAS = {
    ("gpu_setup", "1.0.2"):
        "961f2f3929b26aae4213c879992c609bb71ae4ce88134125a74c88dce9043990",
    ("powercycle_setup", "1.0.1"):
        "38cb525fd5405b0c0f08c765a8d3a3bdbcea7aadf21c0733f59fedbfd9a960e3",
    ("sleep_setup", "1.0.1"):
        "c54ed4b371612e37af3954df07a5588a19adebc5edce74f6205b0f1557f2163e",
}


def _load_versions():
    spec = importlib.util.spec_from_file_location("e7f8_seed", SEED)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.VERSIONS


def test_e7f8_seed_shas_match_entry_scripts_not_lib():
    versions = _load_versions()
    assert {(v["name"], v["ver"]) for v in versions} == set(ENTRY_SHAS), (
        "seed 版本集合变了——本用例的钉值只覆盖原三条"
    )
    for v in versions:
        key = (v["name"], v["ver"])
        assert v["sha"] == ENTRY_SHAS[key], (
            f"{v['name']} v{v['ver']}: seed sha must be entry script, "
            f"got {v['sha'][:12]}… want {ENTRY_SHAS[key][:12]}…"
        )
        assert v["sha"] != LIB_SHAS[key]


def test_manifest_registers_entry_script_for_seeded_versions():
    """发布单元侧的入口登记与 seed 口径一致：``script`` 指 ``<name>.py``，不是 ``_lib.py``。"""
    doc = json.loads((REPO / "tool_manifest.json").read_text(encoding="utf-8"))
    for name, ver in ENTRY_SHAS:
        entries = [e for e in doc["tools"][name]["versions"] if e["version"] == ver]
        assert len(entries) == 1, f"{name}@{ver} 应在 tool_manifest.json 恰有一条登记"
        assert entries[0]["script"] == f"{name}.py"
