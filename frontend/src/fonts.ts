/**
 * Noto Sans SC 的 @font-face 声明，**必须**只经由动态 import 引入。
 *
 * 这个包为覆盖 CJK 字形拆了 101 个 @font-face 块（含若干 emoji 区段），
 * 光 CSS 就 101KB / 31.5KB gzip。静态 import 会把它并进 index.css ——
 * 那是 <head> 里的阻塞样式表，等于让首屏渲染多等一份字体声明，
 * 而声明本身并不画像素：woff2 已是 font-display: swap，本来就先用
 * 回落字体出字再换字。
 *
 * 拆成动态 import 后 Vite 会单独产出一份 CSS，由 JS 在挂载后注入，
 * 关键路径少 31.5KB gzip（CSS 包 58.6 → 27.2KB）。代价是字体切换比
 * 原先稍晚一点发生；因为 --font-sans 的回落是 system-ui（中文机器上
 * 本就是 CJK 字体），首屏不会出现方框或错位排版。
 *
 * 回归检测：`npm run build` 后 dist/index.html 的 <link rel=stylesheet>
 * 只应有一份，且 gzip 后 < 30KB。
 */
import '@fontsource-variable/noto-sans-sc';
