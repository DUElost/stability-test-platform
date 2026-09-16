"""Backend package root."""

# #2341：本包**不再**声明 ``__version__``——平台版本的真值是部署树里的
# ``release-manifest.json``（``backend/core/release_manifest.py`` 读取）。
# 源码常量会与实际跑的 revision 漂移：它自 2026-05-05 引入起从未更新，
# 却让 ``stability_build_info`` 面板恒显示 2.0.0（「有数据但是假的」）。
