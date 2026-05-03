import sys
import tempfile
import tarfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from backend.api.routes.runs import _artifact_download_target
    from backend.services.report_service import _load_risk_summary_from_artifacts
except ModuleNotFoundError:
    from api.routes.runs import _artifact_download_target
    from services.report_service import _load_risk_summary_from_artifacts


class TestArtifactDownloadTarget(unittest.TestCase):
    def test_file_uri_resolves_local_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "run-1.tar.gz"
            artifact_path.write_text("dummy", encoding="utf-8")
            target = _artifact_download_target(f"file://{artifact_path}")
            self.assertEqual(target["kind"], "local")
            self.assertEqual(Path(target["path"]), artifact_path)

    def test_http_uri_returns_redirect(self):
        url = "https://example.com/artifacts/run-1.tar.gz"
        target = _artifact_download_target(url)
        self.assertEqual(target["kind"], "redirect")
        self.assertEqual(target["url"], url)

    def test_missing_file_raises_404(self):
        with self.assertRaises(HTTPException) as ctx:
            _artifact_download_target("file:///tmp/not-exist-xyz.tar.gz")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_load_risk_summary_from_tar_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "runs" / "91"
            run_dir.mkdir(parents=True)
            risk_path = run_dir / "risk_summary.json"
            risk_path.write_text('{"risk_level":"HIGH","counts":{"events_total":2}}', encoding="utf-8")

            archive_path = run_dir.parent / "91.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(run_dir, arcname=run_dir.name)

            artifact = SimpleNamespace(
                storage_uri=f"file://{archive_path}",
                created_at=datetime.now(timezone.utc),
            )
            summary = _load_risk_summary_from_artifacts([artifact])
            self.assertIsNotNone(summary)
            assert summary is not None
            self.assertEqual(summary.get("risk_level"), "HIGH")
            self.assertEqual(summary.get("counts", {}).get("events_total"), 2)


if __name__ == "__main__":
    unittest.main()
