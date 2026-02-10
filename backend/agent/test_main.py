import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from backend.agent.main import complete_run
except ModuleNotFoundError:
    from agent.main import complete_run


class TestAgentMain(unittest.TestCase):
    def test_complete_run_includes_artifact_payload(self):
        artifact = {
            "storage_uri": "file:///tmp/88.tar.gz",
            "size_bytes": 2048,
            "checksum": "b" * 64,
        }
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            complete_run(
                "http://127.0.0.1:8000",
                88,
                {
                    "status": "FINISHED",
                    "exit_code": 0,
                    "error_code": None,
                    "error_message": None,
                    "log_summary": "ok",
                    "artifact": artifact,
                },
            )

            called_payload = mock_post.call_args.kwargs["json"]
            self.assertEqual(called_payload["update"]["status"], "FINISHED")
            self.assertEqual(called_payload["artifact"], artifact)


if __name__ == "__main__":
    unittest.main()
