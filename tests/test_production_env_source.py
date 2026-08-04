"""生产 env 单一事实源（#120）。

这些断言守的是一条**已经差点出事**的路径：`alembic/env.py` 与
`core/database.py` 的兜底默认都写死成 `stp:password@localhost:5432/stp`，
直接点名生产库，只是密码是占位符。今天靠密码错拦住，但只要那个密码对上
（或服务端放开 trust 认证），干净 shell 里一条 `alembic upgrade` 就会静默
改写生产库。

注意「断言库名必须是 stp」这类护栏对上面那个场景**无效** —— 危险路径的默认
值本来就叫 stp。所以真正的守卫是「解析不到就拒绝运行」。
"""

import ast

import pytest

from backend.core.env_source import (
    PRODUCTION_ENV_FILE,
    redact_url,
    resolve_database_url,
)

_URL = "postgresql+psycopg://alice:s3cret@db.internal:5432/prod"


class TestResolveDatabaseUrl:
    def test_ambient_env_wins(self, tmp_path):
        f = tmp_path / ".env.backend"
        f.write_text("DATABASE_URL=postgresql://from:file@h/db\n", encoding="utf-8")
        url, source = resolve_database_url(
            env_file=f, environ={"DATABASE_URL": _URL},
        )
        assert url == _URL
        assert source == "environment"

    def test_falls_back_to_env_backend_file(self, tmp_path):
        f = tmp_path / ".env.backend"
        f.write_text(f"OTHER=1\nDATABASE_URL={_URL}\n", encoding="utf-8")
        url, source = resolve_database_url(env_file=f, environ={})
        assert url == _URL
        assert source == str(f)

    def test_blank_ambient_value_is_not_treated_as_set(self, tmp_path):
        f = tmp_path / ".env.backend"
        f.write_text(f"DATABASE_URL={_URL}\n", encoding="utf-8")
        url, _ = resolve_database_url(env_file=f, environ={"DATABASE_URL": "   "})
        assert url == _URL

    def test_quotes_are_stripped(self, tmp_path):
        f = tmp_path / ".env.backend"
        f.write_text(f'DATABASE_URL="{_URL}"\n', encoding="utf-8")
        assert resolve_database_url(env_file=f, environ={})[0] == _URL

    def test_commented_out_key_is_ignored(self, tmp_path):
        f = tmp_path / ".env.backend"
        f.write_text(f"# DATABASE_URL={_URL}\n", encoding="utf-8")
        with pytest.raises(RuntimeError):
            resolve_database_url(env_file=f, environ={})

    def test_refuses_to_guess_when_nothing_is_configured(self, tmp_path):
        """**核心守卫。**

        旧行为是回落到 `stp:password@localhost:5432/stp` —— 直接点名生产库。
        宁可报错，也不能猜；猜出来的那个默认值正是危险本身。
        """
        with pytest.raises(RuntimeError) as exc:
            resolve_database_url(env_file=tmp_path / "missing", environ={})
        assert "Refusing to guess" in str(exc.value)

    def test_refuses_when_file_exists_but_lacks_the_key(self, tmp_path):
        f = tmp_path / ".env.backend"
        f.write_text("REDIS_URL=redis://localhost/0\n", encoding="utf-8")
        with pytest.raises(RuntimeError):
            resolve_database_url(env_file=f, environ={})

    def test_default_source_is_the_repo_root_env_backend(self):
        """必须是仓库根的 .env.backend —— 不是 backend/.env。

        后者是开发者覆盖文件，让迁移读它就是 DDL 打到错库的成因。
        """
        assert PRODUCTION_ENV_FILE.name == ".env.backend"
        assert PRODUCTION_ENV_FILE.parent.name != "backend"


class TestRedactUrl:
    def test_password_is_stripped(self):
        assert "s3cret" not in redact_url(_URL)
        assert redact_url(_URL) == "postgresql+psycopg://<redacted>@db.internal:5432/prod"

    def test_host_and_database_survive_for_diagnosis(self):
        out = redact_url(_URL)
        assert "db.internal" in out and "prod" in out

    def test_url_without_credentials_is_unchanged(self):
        plain = "postgresql+psycopg://localhost:5432/stp"
        assert redact_url(plain) == plain


class TestAlembicEnvUsesTheSharedResolver:
    def test_no_hardcoded_database_url_default_remains(self):
        """兜底默认必须彻底消失，而不是换个写法留着。

        用 AST 找「真正作为 os.getenv 默认值的字符串」，不做文本匹配 ——
        否则文档里引用旧值做说明也会被误判。
        """
        for rel in ("backend/alembic/env.py", "backend/core/database.py"):
            src = (PRODUCTION_ENV_FILE.parent / rel).read_text(encoding="utf-8")
            self._assert_no_default(src, rel)

    def _scan_getenv_defaults(self, node, offenders):
        """若 node 是 os.getenv("DATABASE_URL", <默认值>) 调用,把默认值登记。"""
        if not isinstance(node, ast.Call):
            return
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != "getenv" or len(node.args) < 2:
            return
        key = node.args[0]
        if isinstance(key, ast.Constant) and key.value == "DATABASE_URL":
            offenders.append(ast.dump(node.args[1]))

    def _assert_no_default(self, src, rel):
        offenders = []
        for node in ast.walk(ast.parse(src)):
            self._scan_getenv_defaults(node, offenders)
        assert not offenders, f"{rel} 仍有 DATABASE_URL 兜底默认: {offenders}"
