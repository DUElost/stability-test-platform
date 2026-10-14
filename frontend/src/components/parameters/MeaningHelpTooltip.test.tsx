import { describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MeaningHelpTooltip } from './MeaningHelpTooltip';

describe('MeaningHelpTooltip', () => {
  it('空释义不渲染触发器', () => {
    const { container } = render(<MeaningHelpTooltip label="巡检间隔" meaning="   " />);
    expect(container).toBeEmptyDOMElement();
  });

  it('渲染带 aria-label 的帮助图标；释义默认不常显为静态段落', async () => {
    const user = userEvent.setup();
    const meaning = '轮间等待；未设则无巡检。';
    render(<MeaningHelpTooltip label="巡检间隔" meaning={meaning} />);

    const trigger = screen.getByRole('button', { name: '巡检间隔的说明' });
    expect(trigger).toHaveAttribute('data-testid', 'meaning-help');
    expect(screen.queryByText(meaning)).not.toBeInTheDocument();

    await user.hover(trigger);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(meaning);
  });

  it('键盘聚焦时可打开 Tooltip', async () => {
    const user = userEvent.setup();
    const meaning = '从首次等待开始计时。';
    render(<MeaningHelpTooltip label="硬顶" meaning={meaning} />);

    await user.tab();
    expect(screen.getByRole('button', { name: '硬顶的说明' })).toHaveFocus();
    expect(await screen.findByRole('tooltip')).toHaveTextContent(meaning);
  });

  it('触屏点按可打开完整释义，再次点按可关闭并可重开', async () => {
    const user = userEvent.setup();
    const meaning = '触屏可读的完整释义。';
    render(<MeaningHelpTooltip label="等待秒数" meaning={meaning} />);

    const trigger = screen.getByRole('button', { name: '等待秒数的说明' });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

    await user.pointer([
      { keys: '[TouchA>]', target: trigger },
      { keys: '[/TouchA]' },
    ]);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(meaning);

    await user.pointer([
      { keys: '[TouchA>]', target: trigger },
      { keys: '[/TouchA]' },
    ]);
    await waitFor(() => {
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    });

    await user.pointer([
      { keys: '[TouchA>]', target: trigger },
      { keys: '[/TouchA]' },
    ]);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(meaning);
  });
});
