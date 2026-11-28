import { useEffect, useState } from 'react';
import { parseIsoToDate } from '@/utils/format';

export function useFinishingClock(deadlines: readonly string[]): number {
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    const timestamps = deadlines.flatMap((deadline) => {
      const date = parseIsoToDate(deadline);
      return date ? [date.getTime()] : [];
    });
    if (timestamps.length === 0) return;
    let timeout: ReturnType<typeof setTimeout>;

    const tick = () => {
      const current = Date.now();
      setNow(current);
      const next = Math.min(...timestamps.filter((deadline) => deadline > current));
      if (Number.isFinite(next)) {
        timeout = setTimeout(tick, Math.min(1000, next - current));
      }
    };

    timeout = setTimeout(tick, 0);
    return () => clearTimeout(timeout);
  }, [deadlines]);

  return now;
}
