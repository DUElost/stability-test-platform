"""ADR-0033 / #3013：DeviceLogEvent 下载（目录 zip / 文件）。"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from backend.services.device_log_event_download import (
    build_device_log_event_download_response,
)


class TestDeviceLogEventDownload(unittest.TestCase):
    def test_missing_or_mismatched_event_404(self):
        db = MagicMock()
        db.get.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            build_device_log_event_download_response(
                db, plan_run_id=1, event_id=uuid4(),
            )
        self.assertEqual(ctx.exception.status_code, 404)

        wrong = MagicMock()
        wrong.plan_run_id = 99
        wrong.state = "REMOTE"
        wrong.remote_path = "/x"
        db.get.return_value = wrong
        with self.assertRaises(HTTPException) as ctx:
            build_device_log_event_download_response(
                db, plan_run_id=1, event_id=uuid4(),
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_pruned_returns_409(self):
        ev = MagicMock()
        ev.plan_run_id = 1
        ev.state = "PRUNED"
        ev.remote_path = "/mnt/x"
        db = MagicMock()
        db.get.return_value = ev
        with self.assertRaises(HTTPException) as ctx:
            build_device_log_event_download_response(
                db, plan_run_id=1, event_id=uuid4(),
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_directory_zip_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            event_dir = root / "devices" / "7" / "ev-a"
            event_dir.mkdir(parents=True)
            (event_dir / "main.dbg").write_text("payload", encoding="utf-8")
            eid = uuid4()
            ev = MagicMock()
            ev.id = eid
            ev.plan_run_id = 7
            ev.state = "REMOTE"
            ev.remote_path = str(event_dir)
            db = MagicMock()
            db.get.return_value = ev
            with patch.dict("os.environ", {"STP_AEE_NFS_ROOT": temp_dir}):
                resp = build_device_log_event_download_response(
                    db, plan_run_id=7, event_id=eid,
                )
            self.assertEqual(resp.media_type, "application/zip")
            with zipfile.ZipFile(resp.path) as zf:
                self.assertIn("main.dbg", zf.namelist())
                self.assertEqual(zf.read("main.dbg"), b"payload")
            Path(resp.path).unlink(missing_ok=True)

    def test_path_outside_devices_400(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = Path(temp_dir) / "other" / "ev"
            outside.mkdir(parents=True)
            (outside / "f").write_text("x", encoding="utf-8")
            # NFS root is sibling so outside is under root but not under devices/
            nfs = Path(temp_dir) / "nfs"
            nfs.mkdir()
            (nfs / "devices").mkdir()
            ev = MagicMock()
            ev.plan_run_id = 1
            ev.state = "REMOTE"
            ev.remote_path = str(outside)
            db = MagicMock()
            db.get.return_value = ev
            with patch.dict("os.environ", {"STP_AEE_NFS_ROOT": str(nfs)}):
                with self.assertRaises(HTTPException) as ctx:
                    build_device_log_event_download_response(
                        db, plan_run_id=1, event_id=uuid4(),
                    )
            self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
