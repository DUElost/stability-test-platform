import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import NotificationsPage from './NotificationsPage';
import type { AlertRule, NotificationChannel, NotificationLog } from '@/utils/api/types';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  confirm: vi.fn(),
  notifications: {
    listChannels: vi.fn(),
    listRules: vi.fn(),
    listLogs: vi.fn(),
    createChannel: vi.fn(),
    updateChannel: vi.fn(),
    deleteChannel: vi.fn(),
    testChannel: vi.fn(),
    createRule: vi.fn(),
    updateRule: vi.fn(),
    deleteRule: vi.fn(),
    markAllRead: vi.fn(),
  },
}));

// toApiError 保留真实实现：渠道测试失败要把后端 detail 原样带到 toast 上，
// mock 掉就验不出这条链路。
vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return { ...actual, api: { ...actual.api, notifications: mocks.notifications } };
});

vi.mock('@/hooks/useToast', () => ({ useToast: () => mocks.toast }));
vi.mock('@/hooks/useConfirm', () => ({ useConfirm: () => mocks.confirm }));

const CHANNEL_WEBHOOK: NotificationChannel = {
  id: 1,
  name: '运维群',
  type: 'WEBHOOK',
  config: { url: 'https://hook.example/ops' },
  enabled: true,
  created_at: '2026-08-01T00:00:00Z',
};

const CHANNEL_EMAIL: NotificationChannel = {
  id: 2,
  name: '值班邮箱',
  type: 'EMAIL',
  config: { to: 'oncall@example.com' },
  enabled: false,
  created_at: '2026-08-01T00:00:00Z',
};

const RULE: AlertRule = {
  id: 10,
  name: '失败即通知',
  event_type: 'RUN_FAILED',
  channel_id: 1,
  channel_name: '运维群',
  filters: {},
  enabled: true,
  created_at: '2026-08-01T00:00:00Z',
};

function makeLog(overrides: Partial<NotificationLog> & { id: number }): NotificationLog {
  return {
    source: 'PLATFORM',
    event_type: 'RUN_FAILED',
    severity: 'warning',
    title: 'PlanRun #1 失败',
    message: '',
    context: {},
    read: true,
    created_at: '2026-08-18T10:00:00Z',
    ...overrides,
  };
}

function setData(opts: {
  channels?: NotificationChannel[];
  rules?: AlertRule[];
  logs?: NotificationLog[];
  logsTotal?: number;
}) {
  const channels = opts.channels ?? [];
  const rules = opts.rules ?? [];
  const logs = opts.logs ?? [];
  const total = opts.logsTotal ?? logs.length;
  mocks.notifications.listChannels.mockResolvedValue({ items: channels, total: channels.length });
  mocks.notifications.listRules.mockResolvedValue({ items: rules, total: rules.length });
  mocks.notifications.listLogs.mockImplementation((skip = 0, limit = 50) =>
    Promise.resolve({ items: logs.slice(skip, skip + limit), total, skip, limit }),
  );
}

function renderPage(route = '/notifications') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <NotificationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 渠道 / 规则卡片没有 testid，靠名称回溯到卡片容器再取行内按钮。 */
function cardOf(name: string): HTMLElement {
  const el = screen.getByText(name).closest('div.px-5');
  if (!el) throw new Error(`card not found: ${name}`);
  return el as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.confirm.mockResolvedValue(true);
  mocks.notifications.createChannel.mockResolvedValue({});
  mocks.notifications.updateChannel.mockResolvedValue({});
  mocks.notifications.deleteChannel.mockResolvedValue(undefined);
  mocks.notifications.testChannel.mockResolvedValue({ ok: true, message: 'ok' });
  mocks.notifications.createRule.mockResolvedValue({});
  mocks.notifications.updateRule.mockResolvedValue({});
  mocks.notifications.deleteRule.mockResolvedValue(undefined);
  mocks.notifications.markAllRead.mockResolvedValue({ ok: true });
  setData({});
});

describe('NotificationsPage', () => {
  describe('分页签', () => {
    it('页签标题带上渠道与规则数量', async () => {
      setData({ channels: [CHANNEL_WEBHOOK, CHANNEL_EMAIL], rules: [RULE] });
      renderPage();

      expect(await screen.findByText('通知渠道 (2)')).toBeInTheDocument();
      expect(screen.getByText('告警规则 (1)')).toBeInTheDocument();
    });

    it('有通知记录且 URL 未指定页签时自动切到通知记录', async () => {
      setData({ channels: [CHANNEL_WEBHOOK], logs: [makeLog({ id: 1 })], logsTotal: 1 });
      renderPage();

      expect(await screen.findByText('共 1 条通知')).toBeInTheDocument();
    });

    it('无通知记录时停留在渠道页签', async () => {
      setData({ channels: [CHANNEL_WEBHOOK] });
      renderPage();

      expect(await screen.findByText('运维群')).toBeInTheDocument();
      expect(screen.queryByText(/共 .* 条通知/)).not.toBeInTheDocument();
    });

    it('URL 显式指定页签时不被自动切换覆盖', async () => {
      setData({ channels: [CHANNEL_WEBHOOK], rules: [RULE], logs: [makeLog({ id: 1 })], logsTotal: 1 });
      renderPage('/notifications?tab=rules');

      expect(await screen.findByText('失败即通知')).toBeInTheDocument();
      expect(screen.queryByText('共 1 条通知')).not.toBeInTheDocument();
    });

    it('自动切换只发生一次，用户切回渠道后不会被再次拽走', async () => {
      setData({ channels: [CHANNEL_WEBHOOK], logs: [makeLog({ id: 1 })], logsTotal: 1 });
      renderPage();
      await screen.findByText('共 1 条通知');

      fireEvent.click(screen.getByText('通知渠道 (1)'));

      expect(screen.getByText('运维群')).toBeInTheDocument();
      expect(screen.queryByText('共 1 条通知')).not.toBeInTheDocument();
    });
  });

  describe('渠道列表', () => {
    it('展示类型中文名、目标地址与启用状态', async () => {
      setData({ channels: [CHANNEL_WEBHOOK, CHANNEL_EMAIL] });
      renderPage();
      await screen.findByText('运维群');

      expect(within(cardOf('运维群')).getByText('Webhook')).toBeInTheDocument();
      expect(within(cardOf('运维群')).getByText('https://hook.example/ops')).toBeInTheDocument();
      // EMAIL 的地址存在 config.to，不是 config.url
      expect(within(cardOf('值班邮箱')).getByText('邮件')).toBeInTheDocument();
      expect(within(cardOf('值班邮箱')).getByText('oncall@example.com')).toBeInTheDocument();
    });

    it('config 里既无 url 也无 to 时显示占位', async () => {
      setData({ channels: [{ ...CHANNEL_WEBHOOK, config: {} }] });
      renderPage();
      await screen.findByText('运维群');

      expect(within(cardOf('运维群')).getByText('-')).toBeInTheDocument();
    });

    it('无渠道时给出空态', async () => {
      renderPage();
      expect(await screen.findByText('暂无通知渠道')).toBeInTheDocument();
    });
  });

  describe('渠道操作', () => {
    it('测试成功提示已发送', async () => {
      setData({ channels: [CHANNEL_WEBHOOK] });
      renderPage();
      await screen.findByText('运维群');

      fireEvent.click(screen.getByLabelText('测试渠道 运维群'));

      await waitFor(() => expect(mocks.toast.success).toHaveBeenCalledWith('测试通知已发送'));
    });

    it('测试失败把后端 detail 原样带到 toast', async () => {
      setData({ channels: [CHANNEL_WEBHOOK] });
      mocks.notifications.testChannel.mockRejectedValue({
        response: { status: 400, data: { detail: 'Webhook 地址不可达' } },
      });
      renderPage();
      await screen.findByText('运维群');

      fireEvent.click(screen.getByLabelText('测试渠道 运维群'));

      await waitFor(() => expect(mocks.toast.error).toHaveBeenCalledWith('Webhook 地址不可达'));
    });

    it('删除需二次确认，取消则不发请求', async () => {
      setData({ channels: [CHANNEL_WEBHOOK] });
      mocks.confirm.mockResolvedValue(false);
      renderPage();
      await screen.findByText('运维群');

      fireEvent.click(screen.getByLabelText('删除渠道 运维群'));

      await waitFor(() => expect(mocks.confirm).toHaveBeenCalled());
      expect(mocks.notifications.deleteChannel).not.toHaveBeenCalled();
    });

    it('确认后按 id 删除', async () => {
      setData({ channels: [CHANNEL_WEBHOOK] });
      renderPage();
      await screen.findByText('运维群');

      fireEvent.click(screen.getByLabelText('删除渠道 运维群'));

      await waitFor(() => expect(mocks.notifications.deleteChannel).toHaveBeenCalledWith(1));
    });
  });

  describe('渠道表单', () => {
    it('WEBHOOK 把地址写进 config.url', async () => {
      renderPage();
      fireEvent.click(await screen.findByText('添加渠道'));

      fireEvent.change(screen.getByPlaceholderText('渠道名称'), { target: { value: '新群' } });
      fireEvent.change(screen.getByPlaceholderText('https://...'), {
        target: { value: 'https://hook.example/new' },
      });
      fireEvent.click(screen.getByText('保存'));

      await waitFor(() =>
        expect(mocks.notifications.createChannel).toHaveBeenCalledWith({
          name: '新群',
          type: 'WEBHOOK',
          config: { url: 'https://hook.example/new' },
          enabled: true,
        }),
      );
    });

    it('EMAIL 把地址写进 config.to，并换掉标签与 placeholder', async () => {
      renderPage();
      fireEvent.click(await screen.findByText('添加渠道'));

      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'EMAIL' } });
      expect(screen.getByText('收件人邮箱')).toBeInTheDocument();

      fireEvent.change(screen.getByPlaceholderText('渠道名称'), { target: { value: '值班' } });
      fireEvent.change(screen.getByPlaceholderText('user@example.com'), {
        target: { value: 'a@b.com' },
      });
      fireEvent.click(screen.getByText('保存'));

      await waitFor(() =>
        expect(mocks.notifications.createChannel).toHaveBeenCalledWith({
          name: '值班',
          type: 'EMAIL',
          config: { to: 'a@b.com' },
          enabled: true,
        }),
      );
    });

    it('名称为空时保存禁用', async () => {
      renderPage();
      fireEvent.click(await screen.findByText('添加渠道'));

      expect(screen.getByText('保存').closest('button')).toBeDisabled();
    });

    it('编辑时回填名称、类型与地址，并走 update 而非 create', async () => {
      setData({ channels: [CHANNEL_EMAIL] });
      renderPage();
      await screen.findByText('值班邮箱');

      fireEvent.click(screen.getByLabelText('编辑渠道 值班邮箱'));

      expect(screen.getByText('编辑渠道')).toBeInTheDocument();
      expect(screen.getByPlaceholderText('渠道名称')).toHaveValue('值班邮箱');
      expect(screen.getByRole('combobox')).toHaveValue('EMAIL');
      // config.to 回填到同一个地址输入框
      expect(screen.getByPlaceholderText('user@example.com')).toHaveValue('oncall@example.com');

      fireEvent.click(screen.getByText('保存'));

      await waitFor(() =>
        expect(mocks.notifications.updateChannel).toHaveBeenCalledWith(2, {
          name: '值班邮箱',
          type: 'EMAIL',
          config: { to: 'oncall@example.com' },
          enabled: false,
        }),
      );
      expect(mocks.notifications.createChannel).not.toHaveBeenCalled();
    });

    it('保存失败提示且不关闭弹窗', async () => {
      mocks.notifications.createChannel.mockRejectedValue(new Error('boom'));
      renderPage();
      fireEvent.click(await screen.findByText('添加渠道'));
      fireEvent.change(screen.getByPlaceholderText('渠道名称'), { target: { value: 'x' } });

      fireEvent.click(screen.getByText('保存'));

      await waitFor(() => expect(mocks.toast.error).toHaveBeenCalledWith('保存失败'));
      expect(screen.getByText('添加渠道', { selector: 'h3' })).toBeInTheDocument();
    });
  });

  describe('规则', () => {
    it('没有渠道时禁止添加规则，并把空态改写成先建渠道', async () => {
      renderPage('/notifications?tab=rules');

      expect(await screen.findByText('请先添加通知渠道')).toBeInTheDocument();
      expect(screen.getByText('添加规则').closest('button')).toBeDisabled();
    });

    it('有渠道但无规则时空态改回缺规则', async () => {
      setData({ channels: [CHANNEL_WEBHOOK] });
      renderPage('/notifications?tab=rules');

      expect(await screen.findByText('暂无告警规则')).toBeInTheDocument();
      expect(screen.getByText('添加规则').closest('button')).not.toBeDisabled();
    });

    it('展示事件类型中文名与目标渠道', async () => {
      setData({ channels: [CHANNEL_WEBHOOK], rules: [RULE] });
      renderPage('/notifications?tab=rules');
      await screen.findByText('失败即通知');

      expect(within(cardOf('失败即通知')).getByText('任务失败')).toBeInTheDocument();
      expect(within(cardOf('失败即通知')).getByText('→ 运维群')).toBeInTheDocument();
    });

    it('缺 channel_name 时回退成渠道编号', async () => {
      setData({
        channels: [CHANNEL_WEBHOOK],
        rules: [{ ...RULE, channel_name: undefined, channel_id: 7 }],
      });
      renderPage('/notifications?tab=rules');
      await screen.findByText('失败即通知');

      expect(within(cardOf('失败即通知')).getByText('→ 渠道 #7')).toBeInTheDocument();
    });

    it('新建规则默认挂在第一个渠道上', async () => {
      setData({ channels: [CHANNEL_WEBHOOK, CHANNEL_EMAIL] });
      renderPage('/notifications?tab=rules');
      fireEvent.click(await screen.findByText('添加规则'));

      fireEvent.change(screen.getByPlaceholderText('规则名称'), { target: { value: '新规则' } });
      fireEvent.click(screen.getByText('保存'));

      await waitFor(() =>
        expect(mocks.notifications.createRule).toHaveBeenCalledWith({
          name: '新规则',
          event_type: 'RUN_FAILED',
          channel_id: 1,
          enabled: true,
        }),
      );
    });

    it('编辑规则走 update 并回填字段', async () => {
      setData({ channels: [CHANNEL_WEBHOOK], rules: [RULE] });
      renderPage('/notifications?tab=rules');
      await screen.findByText('失败即通知');

      fireEvent.click(screen.getByLabelText('编辑规则 失败即通知'));
      expect(screen.getByPlaceholderText('规则名称')).toHaveValue('失败即通知');

      fireEvent.click(screen.getByText('保存'));

      await waitFor(() =>
        expect(mocks.notifications.updateRule).toHaveBeenCalledWith(10, {
          name: '失败即通知',
          event_type: 'RUN_FAILED',
          channel_id: 1,
          enabled: true,
        }),
      );
    });

    it('删除规则需确认', async () => {
      setData({ channels: [CHANNEL_WEBHOOK], rules: [RULE] });
      renderPage('/notifications?tab=rules');
      await screen.findByText('失败即通知');

      fireEvent.click(screen.getByLabelText('删除规则 失败即通知'));

      await waitFor(() => expect(mocks.notifications.deleteRule).toHaveBeenCalledWith(10));
    });
  });

  describe('通知记录', () => {
    it('未读条目带未读标记，已读的不带', async () => {
      setData({
        logs: [
          makeLog({ id: 1, title: '未读告警', read: false }),
          makeLog({ id: 2, title: '已读告警', read: true }),
        ],
      });
      renderPage('/notifications?tab=logs');
      await screen.findByText('未读告警');

      expect(screen.getAllByText('未读')).toHaveLength(1);
    });

    it('来源与严重级别按映射显示，未知来源原样透出', async () => {
      setData({
        logs: [
          makeLog({ id: 1, title: '平台事件', source: 'PLATFORM' }),
          makeLog({ id: 2, title: '监控事件', source: 'ALERTMANAGER', severity: 'critical' }),
          makeLog({ id: 3, title: '未知来源', source: 'THIRD_PARTY' as NotificationLog['source'] }),
        ],
      });
      renderPage('/notifications?tab=logs');
      await screen.findByText('平台事件');

      expect(screen.getByText('平台')).toBeInTheDocument();
      expect(screen.getByText('监控')).toBeInTheDocument();
      expect(screen.getByText('THIRD_PARTY')).toBeInTheDocument();
    });

    it('全部标为已读后刷新列表', async () => {
      setData({ logs: [makeLog({ id: 1, read: false })] });
      renderPage('/notifications?tab=logs');
      await screen.findByText('PlanRun #1 失败');

      fireEvent.click(screen.getByText('全部标为已读'));

      await waitFor(() => expect(mocks.notifications.markAllRead).toHaveBeenCalled());
      // 刷新靠 invalidate 触发重取，不是本地改状态
      await waitFor(() =>
        expect(
          mocks.notifications.listLogs.mock.calls.filter((c) => c[1] === 20).length,
        ).toBeGreaterThan(1),
      );
    });

    it('总数不足一页时不渲染翻页控件', async () => {
      setData({ logs: [makeLog({ id: 1 })] });
      renderPage('/notifications?tab=logs');
      await screen.findByText('PlanRun #1 失败');

      expect(screen.queryByText('下一页')).not.toBeInTheDocument();
    });

    it('翻页按 20 条一页请求下一段', async () => {
      const logs = Array.from({ length: 25 }, (_, i) => makeLog({ id: i + 1, title: `告警 ${i + 1}` }));
      setData({ logs, logsTotal: 25 });
      renderPage('/notifications?tab=logs');
      await screen.findByText('告警 1');
      expect(screen.getByText('1 / 2')).toBeInTheDocument();
      expect(screen.getByText('上一页').closest('button')).toBeDisabled();

      fireEvent.click(screen.getByText('下一页'));

      await waitFor(() => expect(screen.getByText('告警 21')).toBeInTheDocument());
      expect(mocks.notifications.listLogs).toHaveBeenCalledWith(20, 20);
      expect(screen.getByText('2 / 2')).toBeInTheDocument();
      expect(screen.getByText('下一页').closest('button')).toBeDisabled();
    });

    it('无记录时给出空态', async () => {
      renderPage('/notifications?tab=logs');
      expect(await screen.findByText('暂无通知记录')).toBeInTheDocument();
    });
  });
});
