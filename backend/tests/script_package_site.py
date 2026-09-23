"""测试夹具：tmp 站点包源 + ``tool_manifest.json``（ADR-0051 Phase 3 注册输入）。

供 services / api 两侧测试共用：``Site.add()`` 登记一个平台脚本版本并（可选）落
``packages/{name}/{version}.tar.gz``；``retire()`` 翻 ``retired:true``。
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path


def tar_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, body in sorted(files.items()):
            data = body.encode("utf-8")
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Site:
    def __init__(self, tmp_path: Path):
        self.packages_root = tmp_path / "packages"
        self.packages_root.mkdir(exist_ok=True)
        self.manifest = tmp_path / "tool_manifest.json"
        self.doc: dict = {"schema_version": 1, "tools": {}}
        self.write()

    def add(self, name: str, version: str, files: dict[str, str], *, script: str | None = None,
            retired: bool = False, publish: bool = True, sha_override: str | None = None,
            python: str | None = None) -> str:
        blob = tar_bytes(files)
        sha = sha_override or hashlib.sha256(blob).hexdigest()
        if publish:
            (self.packages_root / name).mkdir(exist_ok=True)
            (self.packages_root / name / f"{version}.tar.gz").write_bytes(blob)
        self.doc["tools"].setdefault(name, {"versions": []})["versions"].append({
            "version": version, "package_sha256": sha, "artifact": f"packages/{name}/{version}.tar.gz",
            "python": python, "script": script or f"{name}.py", "retired": retired,
        })
        self.write()
        return sha

    def retire(self, name: str, version: str) -> None:
        for e in self.doc["tools"][name]["versions"]:
            if e["version"] == version:
                e["retired"] = True
        self.write()

    def write(self) -> None:
        self.manifest.write_text(json.dumps(self.doc), encoding="utf-8")

    def env(self, runtime_root: str | None = None) -> dict[str, str]:
        out = {"STP_TOOL_MANIFEST": str(self.manifest), "STP_PACKAGES_ROOT": str(self.packages_root)}
        if runtime_root:
            out["STP_SCRIPT_RUNTIME_ROOT"] = runtime_root
        return out
