import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# #2598：`_artifact_download_target` 随 #2420 的「产物下载双路由收敛为一份实现」
# 下沉到 `backend/services/job_artifact_download.py`，路由侧只剩
# `build_artifact_download_response` 的转调。用例测的是**这条判据本身**
# （`file://` 解析 / 缺失产物 / 越权路径），所以直接跟到 service 层。
# 双写法保留：`sys.path` 里插了 `backend/`，老的裸包名形态仍要能导入。
try:
    from backend.services.job_artifact_download import _artifact_download_target
except ModuleNotFoundError:
    from services.job_artifact_download import _artifact_download_target


class TestArtifactDownloadTarget(unittest.TestCase):
    def test_file_uri_resolves_local_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "run-1.tar.gz"
            artifact_path.write_text("dummy", encoding="utf-8")
            with patch.dict("os.environ", {"STP_AEE_NFS_ROOT": temp_dir}):
                target = _artifact_download_target(f"file://{artifact_path}")
                self.assertEqual(target["kind"], "local")
                self.assertEqual(Path(target["path"]), artifact_path)

    def test_http_uri_returns_redirect(self):
        url = "https://example.com/artifacts/run-1.tar.gz"
        target = _artifact_download_target(url)
        self.assertEqual(target["kind"], "redirect")
        self.assertEqual(target["url"], url)

    def test_missing_file_raises_404(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "not-exist-xyz.tar.gz"
            with patch.dict("os.environ", {"STP_AEE_NFS_ROOT": temp_dir}):
                with self.assertRaises(HTTPException) as ctx:
                    _artifact_download_target(f"file://{missing}")
                self.assertEqual(ctx.exception.status_code, 404)

    def test_file_uri_outside_nfs_root_raises_400(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as outside_dir:
            artifact_path = Path(outside_dir) / "run-1.tar.gz"
            artifact_path.write_text("dummy", encoding="utf-8")
            with patch.dict("os.environ", {"STP_AEE_NFS_ROOT": root_dir}):
                with self.assertRaises(HTTPException) as ctx:
                    _artifact_download_target(f"file://{artifact_path}")
                self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
