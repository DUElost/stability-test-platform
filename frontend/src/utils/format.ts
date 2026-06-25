import { formatLocalDateTime, formatLocalTime, parseIsoToDate } from './time';

export { formatLocalDateTime, formatLocalTime, parseIsoToDate };

export type DurationStyle = 'precise' | 'compact' | 'brief';

/**
 * Format a duration in seconds.
 * - precise: PlanRun timers (e.g. 1h 2m 3s)
 * - compact: host uptime (e.g. 2d 5h)
 * - brief: chain chips (e.g. 45m, 1h 30m)
 */
export function formatDurationSeconds(
  seconds: number | null | undefined,
  style: DurationStyle = 'precise',
  empty = '—',
): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) {
    return empty;
  }
  const total = Math.floor(seconds);

  if (style === 'compact') {
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }

  if (style === 'brief') {
    if (total <= 0) return '';
    const m = Math.floor(total / 60);
    const s = Math.floor(total % 60);
    if (m === 0) return `${s}s`;
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = Math.floor(total % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

/** Elapsed time between two ISO timestamps (step tree). */
export function formatStepDuration(
  startedAt: string | null,
  finishedAt: string | null,
): string {
  if (!startedAt) return '';
  const start = new Date(startedAt).getTime();
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  const diffMs = Math.max(0, end - start);
  return formatDurationSeconds(Math.floor(diffMs / 1000), 'precise', '');
}

/** Storage size when input is already in GB. */
export function formatBytesFromGb(gb: number): string {
  if (gb >= 1024) return `${(gb / 1024).toFixed(1)} TB`;
  return `${gb.toFixed(1)} GB`;
}

/** Ratio as percentage string (e.g. 42%). */
export function formatPercent(
  numerator: number,
  denominator: number,
  decimals = 0,
): string {
  if (!denominator) return '0%';
  return `${((numerator / denominator) * 100).toFixed(decimals)}%`;
}

/** Short local date for tables (zh-CN). */
export function formatLocalDate(value?: string | null): string {
  const date = parseIsoToDate(value);
  if (!date) return '-';
  return date.toLocaleDateString('zh-CN');
}

const DATETIME_FULL: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
};

const DATETIME_SHORT: Intl.DateTimeFormatOptions = {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
};

/** Full datetime with year and seconds (tables, audit logs). */
export function formatDateTimeFull(value?: string | null): string {
  return formatLocalDateTime(value, DATETIME_FULL);
}

/** Compact datetime without year (PlanRun hero, cards). */
export function formatDateTimeShort(value?: string | null): string {
  return formatLocalDateTime(value, DATETIME_SHORT);
}

/** Locale string with 24h clock (event streams). */
export function formatDateTimeLocale(value?: string | null, empty = '-'): string {
  const date = parseIsoToDate(value);
  if (!date) return empty;
  return date.toLocaleString('zh-CN', { hour12: false });
}

/** Raw ISO display for technical timestamps (dispatch gate). */
export function formatIsoCompact(value?: string | null, empty = '—'): string {
  if (!value) return empty;
  return value.replace('T', ' ').replace('Z', ' UTC');
}

/** Time-only label from ISO string (24h); invalid input falls back to raw value. */
export function formatTimeLabel(value?: string | null, empty = '—'): string {
  if (!value) return empty;
  const date = parseIsoToDate(value);
  if (!date) return value;
  return formatLocalTime(value);
}

/** Time-only from a Date instance (live refresh stamps). */
export function formatTimeFromDate(value?: Date | null, empty = '—'): string {
  if (!value || Number.isNaN(value.getTime())) return empty;
  return formatLocalTime(value.toISOString());
}

/** Time-only from epoch milliseconds (e.g. React Query dataUpdatedAt). */
export function formatTimeFromMs(ms?: number | null, empty = '—'): string {
  if (ms == null || !Number.isFinite(ms)) return empty;
  return formatTimeFromDate(new Date(ms), empty);
}
