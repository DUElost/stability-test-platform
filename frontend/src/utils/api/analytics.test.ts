import { describe, expect, it, vi } from 'vitest';

const getMock = vi.fn();

vi.mock('./client', () => ({
  default: { get: (...args: unknown[]) => getMock(...args) },
}));

describe('stats analytics client', () => {
  it('hostFailureRate requests /stats/host-failure-rate with days/limit params and unwraps data', async () => {
    getMock.mockResolvedValueOnce({ data: { items: [], days: 30 } });
    const { stats } = await import('./analytics');

    const result = await stats.hostFailureRate(14, 5);

    expect(getMock).toHaveBeenCalledWith('/stats/host-failure-rate', { params: { days: 14, limit: 5 } });
    expect(result).toEqual({ items: [], days: 30 });
  });

  it('hostFailureRate defaults to days=30, limit=10', async () => {
    getMock.mockResolvedValueOnce({ data: { items: [], days: 30 } });
    const { stats } = await import('./analytics');

    await stats.hostFailureRate();

    expect(getMock).toHaveBeenCalledWith('/stats/host-failure-rate', { params: { days: 30, limit: 10 } });
  });

  it('planSuccessRate requests /stats/plan-success-rate with days/limit params', async () => {
    getMock.mockResolvedValueOnce({ data: { items: [], days: 7 } });
    const { stats } = await import('./analytics');

    const result = await stats.planSuccessRate(7, 3);

    expect(getMock).toHaveBeenCalledWith('/stats/plan-success-rate', { params: { days: 7, limit: 3 } });
    expect(result).toEqual({ items: [], days: 7 });
  });

  it('planRunPassRateTrend requests /stats/plan-run-pass-rate-trend with days param', async () => {
    getMock.mockResolvedValueOnce({ data: { points: [], days: 30 } });
    const { stats } = await import('./analytics');

    const result = await stats.planRunPassRateTrend(30);

    expect(getMock).toHaveBeenCalledWith('/stats/plan-run-pass-rate-trend', { params: { days: 30 } });
    expect(result).toEqual({ points: [], days: 30 });
  });
});
