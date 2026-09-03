# 平台 Logo：宽画布裁切与多尺寸接入

Status: accepted
Class: feature

## Decision

原始 JPEG（2816×1536）中间方形 icon 两侧留白过大，不适合 favicon/侧栏。用背景色差检测 content bbox 后裁成正方形，生成：

- `frontend/public/`：favicon / apple-touch / 192 / 512（静态根路径）
- `frontend/src/assets/brand/`：仅保留实际引用的 32/64 PNG（`BrandLogo`）
- 侧栏、登录/注册页替换原先占位 Zap 蓝块；`index.html` 去掉 data-URI 占位图标

未矢量化：源为栅格抽象图形，栅格多尺寸足够；后续若要 SVG 再单独重绘。

**v2（2026-09-01）**：替换为新设计图 `Gemini_Generated_Image_w2lvzzw2lvzzw2lv（已编辑）.jpeg`（人工裁切近正方形 1391×1463 → 中心裁 1391²），覆盖全部 public/brand 位图；组件路径不变。

**透明底 + 裁剪未用尺寸（2026-09-01 / 09-03）**：白底转透明 PNG；删掉未引用的 `logo-40` / `logo-96` / `stp-logo-master`。

## Verification

```bash
cd frontend && npx tsc --noEmit && npm run build
# 目视侧栏 /login favicon；强刷清缓存
```

## Revisit

若需新母版，从下载目录保留的已编辑 JPEG 再导出；勿往 brand 塞未引用尺寸。
