"""ADR-0033 Phase A3 / #3013：PlanRunArtifact 下载服务。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.services.plan_run_artifact_download import (
    build_plan_run_artifact_download_response,
)


class TestPlanRunArtifactDownload(unittest.TestCase):
    def test_missing_or_mismatched_artifact_404(self):
        db = MagicMock()
        db.get.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            build_plan_run_artifact_download_response(
                db, plan_run_id=1, artifact_id=9,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        wrong = MagicMock()
        wrong.plan_run_id = 2
        wrong.storage_uri = "/x.xls"
        db.get.return_value = wrong
        with self.assertRaises(HTTPException) as ctx:
            build_plan_run_artifact_download_response(
                db, plan_run_id=1, artifact_id=9,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_file_uri_returns_file_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Result.xls"
            path.write_bytes(b"xls")
            art = MagicMock()
            art.plan_run_id = 7
            art.storage_uri = f"file://{path}"
            db = MagicMock()
            db.get.return_value = art
            with patch.dict("os.environ", {"STP_AEE_NFS_ROOT": temp_dir}):
                resp = build_plan_run_artifact_download_response(
                    db, plan_run_id=7, artifact_id=3,
                )
            self.assertEqual(Path(resp.path), path)
            self.assertEqual(resp.filename, "Result.xls")


if __name__ == "__main__":
    unittest.main()
