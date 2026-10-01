/**
 * Spreadsheet cell 安全语义的**唯一共享入口**（批次 B3 / #3237 / 方案 #3561 v1.1）。
 *
 * 这里只解决一件事：STP 生成或复制的文本**第一次进入 spreadsheet 单元格语义边界**时，
 * 不让公式触发前缀被解释为公式，以及不让文本结构（引号 / 换行）改变单元格边界。
 *
 * 边界（#3561 §1.6）：CSV 的分隔符与双引号转义只解决 field framing，
 * **不等价于**公式中和；因此两者分层——先对逻辑字符串值做中和，再做各自现有 framing。
 *
 * 非承诺：本模块只承诺 STP 产出被首次消费时的安全，不承诺目标 spreadsheet
 * 保存为 CSV 后再次打开仍安全（save→reopen durability 明确在 B3 承诺之外）。
 */

/**
 * 会触发公式解释的首字符集合（#3561 §1.1）。
 *
 * = + - @ TAB CR LF NUL 取 OWASP WSTG Latest（WSTG-INJT-21）与 ASVS 5.0 V1.2.10 的并集；
 * 全角 ＝ ＋ － ＠ 属环境相关的保守覆盖——不宣称所有 locale 都会执行它们，
 * 但宁可多中和一次，也不把安全性押在客户端 locale 上。
 *
 * 普通中间字符（ABC-123、A+B、foo@bar）不触发：只看首字符。
 */
const FORMULA_TRIGGER_HEADS = new Set([
  '=',
  '+',
  '-',
  '@',
  '\t',
  '\r',
  '\n',
  '\0',
  '＝',
  '＋',
  '－',
  '＠',
]);

/**
 * 中和一个逻辑字符串单元格的公式触发前缀（#3561 §1.6.2）。
 *
 **只处理 string**。调用方必须在 `String(...)` 强制转换**之前**判断原始类型：
 * 否则 number `-5` 会先变成字符串再被加 apostrophe，从数值 -5 变成文本 '-5，
 * 把导出值直接废掉。
 *
 * @param value 已确定是 string 的原始逻辑值（未做 framing）
 * @returns 首字符命中触发集时前置 `'` 的字符串；否则逐字返回原值
 */
export function neutralizeSpreadsheetCellText(value: string): string {
  const head = value.charAt(0);
  if (head === '') return value;
  return FORMULA_TRIGGER_HEADS.has(head) ? `'${value}` : value;
}

/** clipboard 专用结构字符 → 可见字面转义（#3561 §1.2）。 */
const CLIPBOARD_STRUCTURAL_ESCAPES: Record<string, string> = {
  '\t': '\\t',
  '\r': '\\r',
  '\n': '\\n',
  '\0': '\\0',
};

/**
 * 中和一个将按「一列一行」粘贴进 spreadsheet 的 clipboard 单元格
 * （#3561 §1.6.4）。
 *
 * 与 CSV helper 的差别是这里还要守结构：plain-text clipboard 里一个内嵌 TAB/CR/LF/NUL
 * 会被目标 spreadsheet 当成新的列边界或行边界，于是「一条 serial = 一个 cell」当场失效。
 * 因此先把内嵌控制字符转成**可见字面**（`\t` 两个字符），再执行公式中和，
 * 最后对行首双引号补 apostrophe——目标 spreadsheet 可能把行首 `"`
 * 当文本限定符处理，从而重新暴露后面的危险前缀，或把下一行吞进同一 cell。
 *
 * 记录**之间**的行分隔仍由调用方现有的 newline join 负责，本函数不碰。
 *
 * @param value 单条记录的原始字符串（一条 serial / 一个设备字段）
 * @returns 结构安全且公式前缀已中和的单格文本
 */
export function escapeSpreadsheetClipboardCell(value: string): string {
  const structuralSafe = value.replace(
    /[\t\r\n\0]/g,
    (ch) => CLIPBOARD_STRUCTURAL_ESCAPES[ch] ?? ch,
  );
  const neutralized = neutralizeSpreadsheetCellText(structuralSafe);
  // `neutralizeSpreadsheetCellText` 不管 `"`，所以这里补的是纯 clipboard 结构触发。
  return neutralized.charAt(0) === '"' ? `'${neutralized}` : neutralized;
}
