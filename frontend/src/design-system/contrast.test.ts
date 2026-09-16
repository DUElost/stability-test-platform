/**
 * 主题色对比度守卫（WCAG AA）。
 *
 * 背景：#2027 的回归形态是「类名在、样式失效」——`line-clamp-2` 与 `block` 争
 * `display` 那一次 jsdom 看不出来，颜色那一次同样看不出来（醒目度靠的是一串
 * `text-destructive/70` 的透明度修饰符，`--destructive` 白底仅 3.76:1）。组件测试
 * 只能断言「用了哪个类」，**值本身对不对**必须有另一条判据。
 *
 * 本文件直接读 `index.css` 的令牌定义、按 WCAG 2.x 相对亮度公式算对比度，
 * 把「已裁决达标的取值」钉住：改亮度会让这里红，而不是等用户在界面上发觉。
 * 与 `dependencies-and-quality.md` 里既有的「只降亮度不改色相」修法配套
 * （`--muted-foreground` 46.9%→42%、`--primary` 60%→53.3% 同源）。
 */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

// 本仓习惯从 frontend/ 跑 vitest（npm test / CI `working-directory: frontend`）；
// 也接受从仓库根跑，两种 cwd 都能定位到同一份 index.css。
const CSS_PATH =
  [
    path.resolve(process.cwd(), 'src/index.css'),
    path.resolve(process.cwd(), 'frontend/src/index.css'),
  ].find(existsSync) ?? '';
if (!CSS_PATH) throw new Error('找不到 frontend/src/index.css（cwd 不在预期位置？）');
const CSS = readFileSync(CSS_PATH, 'utf-8');

interface Hsl {
  h: number;
  s: number;
  l: number;
}

/** 取 `selector { … }` 块（花括号配对，避免被嵌套规则截断）。 */
function themeBlock(selector: string): string {
  // 必须按「行首选择器 + {」匹配：`.dark` 在文件顶部 `@custom-variant …(.dark *)`
  // 里先出现过一次，朴素 indexOf 会从那里取到 `@theme inline {` 的块（实测踩过）。
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = new RegExp(`^\\s*${escaped}\\s*\\{`, 'm').exec(CSS);
  if (match === null) throw new Error(`index.css 里找不到选择器块 ${selector}`);
  const start = match.index;
  const open = CSS.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < CSS.length; i += 1) {
    if (CSS[i] === '{') depth += 1;
    else if (CSS[i] === '}') {
      depth -= 1;
      if (depth === 0) return CSS.slice(open + 1, i);
    }
  }
  throw new Error(`选择器 ${selector} 的花括号未闭合`);
}

/** 解析块内 `--name: H S% L%;` 形态的令牌（十六进制/其它形态跳过）。 */
function tokensOf(selector: string): Record<string, Hsl> {
  const out: Record<string, Hsl> = {};
  const re = /--([\w-]+):\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\s*;/g;
  for (const m of themeBlock(selector).matchAll(re)) {
    out[m[1]] = { h: Number(m[2]), s: Number(m[3]), l: Number(m[4]) };
  }
  return out;
}

function hslToRgb({ h, s, l }: Hsl): [number, number, number] {
  const sn = s / 100;
  const ln = l / 100;
  const c = (1 - Math.abs(2 * ln - 1)) * sn;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = ln - c / 2;
  const seg = Math.floor(h / 60) % 6;
  const table: [number, number, number][] = [
    [c, x, 0],
    [x, c, 0],
    [0, c, x],
    [0, x, c],
    [x, 0, c],
    [c, 0, x],
  ];
  const [r, g, b] = table[seg];
  return [r, g, b].map((v) => Math.round((v + m) * 255)) as [number, number, number];
}

function luminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 对比度（1–21）。 */
function contrast(fg: Hsl, bg: Hsl): number {
  const [a, b] = [luminance(hslToRgb(fg)), luminance(hslToRgb(bg))];
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

const AA_NORMAL_TEXT = 4.5;

// [主题, 前景令牌, 背景令牌, 最低对比度, 说明]
const CASES: [string, string, string, number, string][] = [
  [':root', 'destructive-text', 'background', AA_NORMAL_TEXT, '#2027 错误正文（白底）'],
  [':root', 'destructive-text', 'card', AA_NORMAL_TEXT, '#2027 错误正文（卡片底）'],
  [':root', 'destructive-text', 'muted', AA_NORMAL_TEXT, '#2027 错误正文（浅灰底）'],
  ['.dark', 'destructive-text', 'background', AA_NORMAL_TEXT, '#2027 错误正文（深色底）'],
  ['.dark', 'destructive-text', 'card', AA_NORMAL_TEXT, '#2027 错误正文（深色卡片）'],
  // 顺手钉住此前已按同一修法裁决过的取值，防回落
  [':root', 'muted-foreground', 'background', AA_NORMAL_TEXT, '次要文字（白底）'],
  [':root', 'primary', 'background', AA_NORMAL_TEXT, '主色文字（白底）'],
  ['.dark', 'muted-foreground', 'card', AA_NORMAL_TEXT, '次要文字（深色卡片）'],
];

describe('主题色对比度（WCAG AA）', () => {
  it.each(CASES)('%s：%s on %s ≥ %s（%s）', (theme, fg, bg, min, why) => {
    const tokens = tokensOf(theme);
    expect(tokens[fg], `${theme} 缺少令牌 --${fg}`).toBeDefined();
    expect(tokens[bg], `${theme} 缺少令牌 --${bg}`).toBeDefined();
    const ratio = contrast(tokens[fg], tokens[bg]);
    // why 一并带进失败信息，方便判断该调哪一侧
    expect(ratio, `${why}：--${fg} on --${bg} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
  });

  it('destructive-text 与 destructive 是两个用途不同的令牌（不合并）', () => {
    const light = tokensOf(':root');
    const dark = tokensOf('.dark');
    // 文字令牌必须比填充令牌更深（亮色）/更亮（深色），否则 AA 无从谈起
    expect(light['destructive-text'].l).toBeLessThan(light.destructive.l);
    expect(dark['destructive-text'].l).toBeGreaterThan(dark.destructive.l);
  });
});
