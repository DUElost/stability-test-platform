/** datetime-local 值按浏览器本地时区解释，转为 UTC ISO 供 API 过滤。 */
export function datetimeLocalInputToIso(value: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString();
}

export function parseIsoToDate(value?: string | null): Date | null {
  if (!value) return null;
  const hasTz = /[zZ]|[+-]\d{2}:\d{2}$/.test(value);
  const normalized = hasTz ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatLocalDateTime(
  value?: string | null,
  options: Intl.DateTimeFormatOptions = {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }
): string {
  const date = parseIsoToDate(value);
  if (!date) return '-';
  return date.toLocaleString('zh-CN', options);
}

export function formatLocalTime(value?: string | null): string {
  const date = parseIsoToDate(value);
  if (!date) return '--:--:--';
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}
