import logging
import uuid

import pytest

from backend.agent.identity import generate_agent_instance_id, read_boot_id


class TestGenerateAgentInstanceId:
    def test_returns_32_char_hex(self):
        result = generate_agent_instance_id()
        assert isinstance(result, str)
        assert len(result) == 32
        int(result, 16)  # valid hex

    def test_unique_per_call(self):
        a = generate_agent_instance_id()
        b = generate_agent_instance_id()
        assert a != b


class TestReadBootId:
    def test_linux_reads_boot_id(self, monkeypatch, tmp_path):
        boot_id_file = tmp_path / "boot_id"
        boot_id_file.write_text("abc-def-boot-id\n")

        monkeypatch.setattr("platform.system", lambda: "Linux")
        _real_open = open

        def _mock_open(path, mode="r"):
            if path == "/proc/sys/kernel/random/boot_id":
                return _real_open(str(boot_id_file))

            return _real_open(path, mode)

        monkeypatch.setattr("builtins.open", _mock_open)

        result = read_boot_id()
        assert result == "abc-def-boot-id"

    def test_linux_missing_file_returns_pseudo(self, monkeypatch, caplog):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("builtins.open", lambda path, mode="r": (_ for _ in ()).throw(FileNotFoundError))

        with caplog.at_level(logging.WARNING):
            result = read_boot_id()

        assert len(result) == 32  # uuid4 hex
        int(result, 16)
        assert "pseudo boot_id" in caplog.text

    def test_linux_empty_file_returns_pseudo(self, monkeypatch, tmp_path, caplog):
        boot_id_file = tmp_path / "boot_id"
        boot_id_file.write_text("")

        monkeypatch.setattr("platform.system", lambda: "Linux")

        def _open(path, mode="r"):
            if path == "/proc/sys/kernel/random/boot_id":
                return open(str(boot_id_file))

            raise FileNotFoundError

        monkeypatch.setattr("builtins.open", _open)

        with caplog.at_level(logging.WARNING):
            result = read_boot_id()

        assert len(result) == 32
        assert "pseudo boot_id" in caplog.text

    def test_non_linux_returns_pseudo(self, monkeypatch, caplog):
        monkeypatch.setattr("platform.system", lambda: "Windows")

        with caplog.at_level(logging.WARNING):
            result = read_boot_id()

        assert len(result) == 32
        int(result, 16)
        assert "pseudo boot_id" in caplog.text
