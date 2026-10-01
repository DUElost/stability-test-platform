import { describe, expect, it } from 'vitest';
import { escapeSpreadsheetClipboardCell, neutralizeSpreadsheetCellText } from './spreadsheet';

/**
 * 批次 B3 / #3237 / 方案 #3561 v1.1 §4.1 的共享 contract 反例。
 *
 * 本文件断的是「触发集本身」——负向变异（把某个触发字符从集合里拿掉）
 * 必须让这里变红，证明调用点测试不是靠别的东西在挡。
 */
describe('neutralizeSpreadsheetCellText（#3237 共享 contract）', () => {
  it('ASCII 公式触发前缀全部中和', () => {
    // OWASP WSTG-INJT-21 / ASVS V1.2.10 的并集
    expect(neutralizeSpreadsheetCellText('=1+1')).toBe("'=1+1");
    expect(neutralizeSpreadsheetCellText('+1+1')).toBe("'+1+1");
    expect(neutralizeSpreadsheetCellText('-1+1')).toBe("'-1+1");
    expect(neutralizeSpreadsheetCellText('@SUM(1,1)')).toBe("'@SUM(1,1)");
  });

  it('控制字符作首字符时也中和（WSTG 的 TAB/CR/LF，ASVS 的 TAB/NUL）', () => {
    expect(neutralizeSpreadsheetCellText('\t=1+1')).toBe("'\t=1+1");
    expect(neutralizeSpreadsheetCellText('\r=1+1')).toBe("'\r=1+1");
    expect(neutralizeSpreadsheetCellText('\n=1+1')).toBe("'\n=1+1");
    expect(neutralizeSpreadsheetCellText('\0=1+1')).toBe("'\0=1+1");
  });

  it('全角触发前缀也中和（locale 相关的保守覆盖）', () => {
    expect(neutralizeSpreadsheetCellText('＝1+1')).toBe("'＝1+1");
    expect(neutralizeSpreadsheetCellText('＋1+1')).toBe("'＋1+1");
    expect(neutralizeSpreadsheetCellText('－1+1')).toBe("'－1+1");
    expect(neutralizeSpreadsheetCellText('＠SUM(1,1)')).toBe("'＠SUM(1,1)");
  });

  it('只中和首字符——中间位置的触发字符不动', () => {
    // 只看首字符：ABC-123 / A+B / foo@bar 是设备领域极常见的正常串
    expect(neutralizeSpreadsheetCellText('ABC-123')).toBe('ABC-123');
    expect(neutralizeSpreadsheetCellText('A+B')).toBe('A+B');
    expect(neutralizeSpreadsheetCellText('foo@bar')).toBe('foo@bar');
    expect(neutralizeSpreadsheetCellText('a=1,b=2')).toBe('a=1,b=2');
    expect(neutralizeSpreadsheetCellText('serial=ABC-123')).toBe('serial=ABC-123');
  });

  it('普通 quoted 文本不做任何全局删引号/改写', () => {
    // 中和只加前缀，不重写内容：引号、逗号、空格原样保留
    expect(neutralizeSpreadsheetCellText('say "hi"')).toBe('say "hi"');
    expect(neutralizeSpreadsheetCellText('a,b c')).toBe('a,b c');
  });

  it('空串原样返回（不加孤立 apostrophe）', () => {
    expect(neutralizeSpreadsheetCellText('')).toBe('');
  });
});

describe('escapeSpreadsheetClipboardCell（#3237 clipboard-only 结构 contract）', () => {
  it('同样守公式触发前缀', () => {
    expect(escapeSpreadsheetClipboardCell('=1+1')).toBe("'=1+1");
    expect(escapeSpreadsheetClipboardCell('@SUM(1,1)')).toBe("'@SUM(1,1)");
  });

  it('行首双引号：不能因目标 spreadsheet 处理行首引号而重新暴露 =', () => {
    // #3561 v1.1 H4 R1：行首 " 可能被当作文本限定符，重新暴露后面的危险前缀。
    expect(escapeSpreadsheetClipboardCell('"=1+1')).toBe('\'"=1+1');
  });

  it('行首引号不得把下一条记录吞进同一 cell（结构不变量由调用方 join 保证）', () => {
    // 这里只断单格输出不含未转义的结构字符；记录分隔由调用方的 newline join 负责。
    const cell = escapeSpreadsheetClipboardCell('"a');
    expect(cell).toBe('\'"a');
    expect(cell).not.toMatch(/[\t\r\n\0]/);
  });

  it('内嵌 TAB/CR/LF/NUL 转为可见字面转义，不制造新 cell/row', () => {
    expect(escapeSpreadsheetClipboardCell('a\tb')).toBe('a\\tb');
    expect(escapeSpreadsheetClipboardCell('a\rb')).toBe('a\\rb');
    expect(escapeSpreadsheetClipboardCell('a\nb')).toBe('a\\nb');
    expect(escapeSpreadsheetClipboardCell('a\0b')).toBe('a\\0b');
  });

  it('首字符控制字符先转义、再判定——转义后不再以触发字符开头', () => {
    // '\t=1+1'：TAB 先变可见字面 '\t=1+1'，首字符已是 '\'，不需要再加 apostrophe；
    // 但 '=' 仍在格内且不再位于首位，不会被当作公式前缀。
    expect(escapeSpreadsheetClipboardCell('\t=1+1')).toBe('\\t=1+1');
    expect(escapeSpreadsheetClipboardCell('\n=1+1')).toBe('\\n=1+1');
  });

  it('普通 serial 原样返回', () => {
    expect(escapeSpreadsheetClipboardCell('ABC-123')).toBe('ABC-123');
    expect(escapeSpreadsheetClipboardCell('A')).toBe('A');
    expect(escapeSpreadsheetClipboardCell('foo@bar')).toBe('foo@bar');
  });

  it('转义后的输出永不含裸控制字符', () => {
    for (const input of ['=1+1', '"a', 'a\tb\rc\nd\0e', '@x']) {
      expect(escapeSpreadsheetClipboardCell(input)).not.toMatch(/[\t\r\n\0]/);
    }
  });
});
