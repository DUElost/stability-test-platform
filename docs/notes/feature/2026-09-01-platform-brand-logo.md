# 平台 Logo：宽画布裁切与多尺寸接入

Status: preview
Class: feature

## Decision

原始 JPEG（2816×1536）中间方形 icon 两侧留白过大，不适合 favicon/侧栏。用背景色差检测 content bbox 后裁成正方形，生成：

- `frontend/public/`：favicon / apple-touch / 192 / 512（静态根路径）
- `frontend/src/assets/brand/`：32/40/64/96 PNG（`BrandLogo` 组件引用）
- 侧栏、登录/注册页替换原先占位 Zap 蓝块；`index.html` 去掉 data-URI 占位图标

未矢量化：源为栅格抽象图形，栅格多尺寸足够；后续若要 SVG 再单独重绘。

## Verification

```bash
cd frontend && npx tsc --noEmit && npm run build
# 目视侧栏 /login favicon；强刷清缓存
```

## Revisit

若需透明底（去灰底）或更紧裁白圆角卡，重跑裁切脚本并覆盖 brand/public。
