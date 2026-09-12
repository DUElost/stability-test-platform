#!/usr/bin/env bash
# stp-agent-priv 容器冒烟（#1250 / ADR-0037）。
#
# 宿主模式（默认）：只做 docker 编组，把本脚本原样送进一次性容器执行；
#   脚本自身在宿主上不执行任何提权/写系统路径的操作。
# 容器模式：在隔离容器的 root 下跑真实子命令，验证
#   「安装目录外提权写被拒 / 热更新所需操作可用 / symlink 安全」。
#
# 用法（需本机 docker；不触碰宿主文件系统，仅以只读方式挂载仓库）：
#     bash tools/dev/stp_agent_priv_smoke.sh [仓库根路径，默认当前目录]
set -euo pipefail

if [ ! -e /.dockerenv ]; then
    REPO_ROOT="$(cd "${1:-$(pwd)}" && pwd)"
    if ! command -v docker >/dev/null 2>&1; then
        echo "docker is required to run this smoke (host mode never executes payloads)" >&2
        exit 1
    fi
    exec docker run --rm -i -v "$REPO_ROOT":/src:ro python:3.11-slim bash -s < "$0"
fi

# ── 以下只在容器内执行 ────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq rsync >/dev/null 2>&1

# 容器内补 /usr/bin/python3（宿主上由发行版提供；wrapper shebang 不变）
if [ ! -e /usr/bin/python3 ]; then ln -sf "$(command -v python3)" /usr/bin/python3; fi

useradd -r -m -d /srv/stp agentuser 2>/dev/null || true
mkdir -p /usr/local/sbin /srv/stp/agent/resources/mtbf /srv/stp/schemas /srv/stp/logs
cp /src/backend/agent/stp_agent_priv.py /usr/local/sbin/stp-agent-priv
chmod 0755 /usr/local/sbin/stp-agent-priv

echo "stale" > /srv/stp/agent/old.py
echo "host-apk" > /srv/stp/agent/resources/mtbf/apk.bin
printf 'HOST_ID=x\nAPI_URL=http://cp\n' > /srv/stp/.env

echo "== 1) bootstrap =="
/usr/local/sbin/stp-agent-priv bootstrap --install-dir /srv/stp \
  --user agentuser --group agentuser --service stability-test-agent
grep -c NOPASSWD /etc/sudoers.d/stability-test-agent
if grep -qE 'NOPASSWD: /usr/bin/(rsync|cp|chmod|chown|ln)' /etc/sudoers.d/stability-test-agent; then
  echo "BAD: broad rules still present"; exit 1
fi
grep -q 'NOPASSWD: /usr/local/sbin/stp-agent-priv' /etc/sudoers.d/stability-test-agent
echo "SUDOERS_OK"

echo "== 1b) 锚点护栏（#1553）=="
# 把 INSTALL_DIR 移向系统目录必须被拒：否则 fix-ownership 的 `chown -R` 与
# apply-code 的 rsync 会以 root 作用在 /etc、/usr/local 这类目录上。
for bad_dir in /etc / /usr/local /usr/local/sbin; do
  if /usr/local/sbin/stp-agent-priv bootstrap --install-dir "$bad_dir" \
      --user agentuser --group agentuser --service stability-test-agent 2>/dev/null; then
    echo "BAD: bootstrap accepted --install-dir $bad_dir"; exit 1
  fi
done
echo "INSTALL_DIR_GUARD_OK"
# 已存在 conf 时不得重新指向（即使目标目录本身合法）
mkdir -p /srv/other
if /usr/local/sbin/stp-agent-priv bootstrap --install-dir /srv/other \
    --user agentuser --group agentuser --service stability-test-agent 2>/dev/null; then
  echo "BAD: bootstrap re-pointed an existing install"; exit 1
fi
grep -q '^INSTALL_DIR=/srv/stp$' /etc/stp-agent-priv.conf
echo "ANCHOR_DRIFT_GUARD_OK"

echo "== 2) selftest =="
/usr/local/sbin/stp-agent-priv selftest

echo "== 3) apply-code =="
rm -rf /tmp/stage && mkdir -p /tmp/stage/resources/aimonkey
echo "new" > /tmp/stage/main.py
echo "new-tool" > /tmp/stage/resources/aimonkey/tool.bin
chown -R agentuser:agentuser /tmp/stage
SUDO_UID="$(id -u agentuser)" SUDO_GID="$(id -g agentuser)" \
  /usr/local/sbin/stp-agent-priv apply-code --staged /tmp/stage
test -f /srv/stp/agent/main.py && echo "CODE_SYNCED"
test ! -e /srv/stp/agent/old.py && echo "DELETE_SYNCED"
test -f /srv/stp/agent/resources/mtbf/apk.bin && echo "MTBF_KEPT"
if SUDO_UID="$(id -u agentuser)" /usr/local/sbin/stp-agent-priv apply-code --staged /etc 2>/dev/null; then
  echo "BAD: /etc staged accepted"; exit 1
else
  echo "REFUSED_ETC"
fi
mkdir -p /tmp/stage-root && chown root:root /tmp/stage-root
if SUDO_UID="$(id -u agentuser)" /usr/local/sbin/stp-agent-priv apply-code --staged /tmp/stage-root 2>/dev/null; then
  echo "BAD: root-owned staged dir accepted for agent caller"; exit 1
else
  echo "REFUSED_FOREIGN_STAGE"
fi

echo "== 4) install-schema =="
cp /src/backend/schemas/pipeline_schema.json /tmp/stage/pipeline_schema.json
chown agentuser:agentuser /tmp/stage/pipeline_schema.json
SUDO_UID="$(id -u agentuser)" /usr/local/sbin/stp-agent-priv install-schema --file /tmp/stage/pipeline_schema.json
test -f /srv/stp/schemas/pipeline_schema.json && echo "SCHEMA_OK"
if SUDO_UID="$(id -u agentuser)" /usr/local/sbin/stp-agent-priv install-schema --file /etc/passwd 2>/dev/null; then
  echo "BAD: /etc/passwd accepted as schema"; exit 1
else
  echo "REFUSED_PASSWD"
fi

echo "== 5) write-version / deps-marker =="
/usr/local/sbin/stp-agent-priv write-version --version abc1234
test "$(cat /srv/stp/agent/VERSION)" = "abc1234" && echo "VERSION_OK"
if /usr/local/sbin/stp-agent-priv write-version --version 'x;rm -rf /' 2>/dev/null; then
  echo "BAD: version injection accepted"; exit 1
else
  echo "REFUSED_VERSION"
fi
/usr/local/sbin/stp-agent-priv deps-marker --sha "$(printf 'a%.0s' $(seq 1 64))"

echo "== 6) sync-env =="
chown agentuser:agentuser /srv/stp/.env && chmod 640 /srv/stp/.env
/usr/local/sbin/stp-agent-priv sync-env --secret-b64 "$(printf 's3cret' | base64 -w0)"
grep -q '^AGENT_SECRET=s3cret$' /srv/stp/.env && echo "SECRET_OK"
test "$(stat -c '%U %a' /srv/stp/.env)" = "agentuser 640" && echo "OWNER_PRESERVED"
/usr/local/sbin/stp-agent-priv sync-env \
  --overrides-b64 "$(printf '{"LOG_LEVEL":"DEBUG"}' | base64 -w0)" \
  --path-keys-b64 "$(printf '[]' | base64 -w0)"
grep -q '^LOG_LEVEL=DEBUG$' /srv/stp/.env && echo "OVERRIDE_OK"

echo "== 7) fix-ownership（symlink 安全）=="
ln -sf /etc/passwd /srv/stp/agent/evil-link
touch /srv/stp/agent/rootfile && chown root:root /srv/stp/agent/rootfile
/usr/local/sbin/stp-agent-priv fix-ownership
test "$(stat -c '%U' /srv/stp/agent/rootfile)" = "agentuser" && echo "CHOWN_APPLIED"
test "$(stat -c '%U' /etc/passwd)" = "root" && echo "SYMLINK_TARGET_SAFE"

echo "== 8) restart（stub systemctl）=="
printf '#!/bin/sh\necho "STUB systemctl $*"\n' > /usr/bin/systemctl
chmod 0755 /usr/bin/systemctl
/usr/local/sbin/stp-agent-priv restart

echo "ALL_OK"
