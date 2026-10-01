import { hostLabel } from '@/utils/hostDisplay';
import { escapeSpreadsheetClipboardCell, neutralizeSpreadsheetCellText } from '@/utils/spreadsheet';
export interface ExportDeviceRow {
  serial: string;
  host_id?: string | number | null;
  model?: string | null;
  build_display_id?: string | null;
}

export interface HostLabelLookup {
  get(hostId: string): { ip?: string | null; name?: string | null } | undefined;
}

function hostLabelFor(device: ExportDeviceRow, hostMap: HostLabelLookup): string {
  const hostId = String(device.host_id ?? 'unassigned');
  const host = hostMap.get(hostId);
  return hostLabel(host, hostId);
}

/**
 * #3237：先对逻辑字符串值做公式前缀中和，再做原有的逗号/引号/换行 framing。
 * 外层有双引号只解决 field framing，**不等于**公式中和——两层不能互相替代。
 * 既有 escaping 行为不回退。
 */
function csvEscape(value: string): string {
  const neutralized = neutralizeSpreadsheetCellText(value);
  if (/[",\n\r]/.test(neutralized)) return `"${neutralized.replace(/"/g, '""')}"`;
  return neutralized;
}

/** Newline-separated serials for clipboard paste into reports. */
export function formatSerialsClipboard(devices: Array<{ serial: string }>): string {
  // #3237：serial 是 ADB/API 的外部可控值。粘贴进 spreadsheet 时一格一条，内嵌
  // TAB/CR/LF/NUL 与行首 `"` 都会破坏「一条 serial = 一个 cell」，故先过结构 + 公式
  // 中和；记录**之间**的换行仍由这里的 join 负责。
  return devices.map((d) => escapeSpreadsheetClipboardCell(d.serial)).join('\n');
}

/** CSV with serial, host, model, version. */
export function buildDeviceSelectionCsv(devices: ExportDeviceRow[], hostMap: HostLabelLookup): string {
  const lines = ['serial,host,model,version'];
  for (const device of devices) {
    lines.push([
      csvEscape(device.serial),
      csvEscape(hostLabelFor(device, hostMap)),
      csvEscape(device.model || ''),
      csvEscape(device.build_display_id || ''),
    ].join(','));
  }
  return `${lines.join('\n')}\n`;
}

export function downloadTextFile(filename: string, content: string, mime = 'text/csv;charset=utf-8'): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
