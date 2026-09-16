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


/**
 * 后端 **naive** 时间戳（`Column(DateTime)`，无时区）→ 原样展示（#2358）。
 *
 * 为什么是「原样」：PG 把 aware 值按**会话时区**落到 `timestamp without time zone`
 * 再剥掉偏移，读回即**本地墙上时间**——生产实测 `users.last_login` 与本地 `now()`
 * 同刻度（与 UTC 差 8h）。这类值若再按 UTC 解析（见 `parseIsoToDate` 的 `${value}Z`）
 * 会平白 +8，且恰好是「未来时间」这种最刺眼的错法。
 *
 * 带时区的值（`Column(DateTime(timezone=True))`）请继续用 `formatLocalDateTime`。
 */
export function formatNaiveLocalDateTime(value?: string | null, empty = '-'): string {
  if (!value) return empty;
  // 只做形态归一（T → 空格、秒级截断），不做任何时区换算
  return value.replace('T', ' ').slice(0, 19);
}
