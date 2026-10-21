import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DeviceReturnDebtItem } from '@/utils/api/types';

const mockReturnDebts = vi.fn();
const mockReturnOverride = vi.fn();

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      devices: {
        ...actual.api.devices,
        returnDebts: (...args: unknown[]) => mockReturnDebts(...args),
        returnOverride: (...args: unknown[]) => mockReturnOverride(...args),
      },
    },
  };
});

import { DeviceReturnPanel } from './DeviceReturnPanel';

const DEBT: DeviceReturnDebtItem = {
  id: 11,
  family: 'power-cycle',
  source_kind: 'job',
  source_job_id: 9527,
  cause: 'not_executed',
  created_at: '2026-10-19T02:00:00Z',
};

function wrapper(children: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const RETURN_STATE = {
  'power-cycle': {
    state: 'debt',
    source: 'job',
    updated_at: '2026-10-19T02:00:00Z',
  },
  sleep: {
    state: 'unverified',
    source: 'override',
    updated_at: '2026-10-19T03:00:00Z',
  },
};

describe('DeviceReturnPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockReturnDebts.mockResolvedValue([DEBT]);
  });

  it('shows aggregate status, per-family state/source/time and open debts', async () => {
    render(wrapper(
      <DeviceReturnPanel deviceId={7} returnState={RETURN_STATE} isAdmin={false} />,
    ));
    expect(screen.getByTestId('device-return-summary-7')).toHaveTextContent('欠账');
    expect(screen.getByTestId('device-return-family-7-power-cycle')).toHaveTextContent('开关机');
    expect(screen.getByTestId('device-return-family-7-power-cycle')).toHaveTextContent('欠账');
    expect(screen.getByTestId('device-return-family-7-power-cycle')).toHaveTextContent('来源：job');
    expect(screen.getByTestId('device-return-family-7-power-cycle')).toHaveTextContent('更新：');
    expect(screen.getByTestId('device-return-family-7-sleep')).toHaveTextContent('未验证');
    expect(screen.getByTestId('device-return-family-7-sleep')).toHaveTextContent('来源：人工放行');
    expect(await screen.findByTestId('device-return-debt-11')).toHaveTextContent('开关机');
    expect(screen.getByTestId('device-return-debt-11')).toHaveTextContent('未执行');
    expect(screen.getByTestId('device-return-debt-11')).toHaveTextContent('来源：job #9527');
    expect(screen.getByTestId('device-return-debt-11')).toHaveTextContent('建立：');
  });

  it('shows uninitialized + empty debt state without errors (pre-activation)', async () => {
    mockReturnDebts.mockResolvedValue([]);
    render(wrapper(<DeviceReturnPanel deviceId={8} returnState={null} isAdmin={false} />));
    expect(screen.getByTestId('device-return-summary-8')).toHaveTextContent('未初始化');
    expect(screen.getByTestId('device-return-empty-8')).toHaveTextContent('不推断为已归还');
    expect(await screen.findByTestId('device-return-debts-empty-8')).toHaveTextContent('无未关闭条目');
  });

  it('shows unknown family/state as raw values', async () => {
    mockReturnDebts.mockResolvedValue([]);
    render(wrapper(
      <DeviceReturnPanel
        deviceId={9}
        returnState={{ 'new-family': { state: 'weird_state', source: 'new_source' } }}
        isAdmin={false}
      />,
    ));
    const row = screen.getByTestId('device-return-family-9-new-family');
    expect(row).toHaveTextContent('new-family');
    expect(row).toHaveTextContent('weird_state');
    expect(row).toHaveTextContent('来源：new_source');
  });

  it('hides the override entry for non-admin users', async () => {
    render(wrapper(<DeviceReturnPanel deviceId={7} returnState={RETURN_STATE} isAdmin={false} />));
    await screen.findByTestId('device-return-debt-11');
    expect(screen.queryByTestId('device-return-override-7')).not.toBeInTheDocument();
  });

  it('opens the admin override dialog and refreshes after a successful submit', async () => {
    mockReturnOverride.mockResolvedValue({
      device_id: 7,
      families: ['power-cycle'],
      closed_count: 1,
      debt_lease_released: true,
      open_debt_count: 0,
    });
    render(wrapper(<DeviceReturnPanel deviceId={7} returnState={RETURN_STATE} isAdmin />));
    await userEvent.click(screen.getByTestId('device-return-override-7'));
    expect(screen.getByTestId('return-override-dialog')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('专项 开关机'));
    await userEvent.selectOptions(screen.getByLabelText('原因类别'), 'flashed');
    await userEvent.type(screen.getByLabelText('证据引用'), 'job-9527');
    await userEvent.click(screen.getByTestId('return-override-submit'));

    await waitFor(() => expect(mockReturnOverride).toHaveBeenCalledWith(7, {
      families: ['power-cycle'],
      reason_category: 'flashed',
      evidence_ref: 'job-9527',
    }));
    expect(await screen.findByTestId('return-override-result')).toHaveTextContent('已关闭 1 条欠账');
    expect(screen.getByTestId('return-override-result')).toHaveTextContent('已释放欠账租约');
    expect(screen.getByTestId('return-override-result')).toHaveTextContent('剩余 0 条');
  });
});
