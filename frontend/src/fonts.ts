/**
 * 字体 @font-face 声明，**必须**只经由动态 import 引入（见 main.tsx）。
 *
 * - Plus Jakarta Sans / Geist Mono：Latin 层，体积可控，进本 chunk。
 * - Noto Sans SC Variable：CJK 拆了 101 个 @font-face（CSS ~101KB /
 *   31.5KB gzip）。若静态 import 进 index.css，会阻塞首屏；拆到本文件后
 *   Vite 单独产出 CSS，由 JS 挂载后注入。
 *
 * --font-sans 回落含 system-ui（中文机本就有 CJK），首屏不会方框/错位。
 * 回归：`npm run build` 后 dist/index.html 的阻塞 stylesheet 应仍只有一份，
 * 且 gzip 后大体 < 30KB（Jakarta Latin 增量可接受）。
 */
import '@fontsource-variable/plus-jakarta-sans';
import '@fontsource/geist-mono';
import '@fontsource-variable/noto-sans-sc';
