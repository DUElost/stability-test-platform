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
  return host?.ip || host?.name || (hostId === 'unassigned' ? '未分配节点' : hostId);
}

function csvEscape(value: string): string {
  if (/[",\n\r]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

/** Newline-separated serials for clipboard paste into reports. */
export function formatSerialsClipboard(devices: Array<{ serial: string }>): string {
  return devices.map((d) => d.serial).join('\n');
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
