import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DeviceReturnOverrideDialog } from './DeviceReturnOverrideDialog';

const baseProps = {
  isOpen: true,
  isSubmitting: false,
  result: null,
  onClose: vi.fn(),
  onSubmit: vi.fn(),
};

describe('DeviceReturnOverrideDialog', () => {
  it('requires a family, a reason category and an evidence reference', async () => {
    const onSubmit = vi.fn();
    render(<DeviceReturnOverrideDialog {...baseProps} onSubmit={onSubmit} />);
    await userEvent.click(screen.getByTestId('return-override-submit'));
    expect(screen.getByText('请选择至少一个专项，或勾选「全部专项」')).toBeInTheDocument();
    expect(screen.getByText('请选择原因类别', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('请填写证据引用（必填）')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submits the selected families, reason category and trimmed evidence', async () => {
    const onSubmit = vi.fn();
    render(<DeviceReturnOverrideDialog {...baseProps} onSubmit={onSubmit} />);
    await userEvent.click(screen.getByLabelText('专项 休眠'));
    await userEvent.selectOptions(screen.getByLabelText('原因类别'), 'factory_reset');
    await userEvent.type(screen.getByLabelText('证据引用'), '  ticket-42  ');
    await userEvent.click(screen.getByTestId('return-override-submit'));
    expect(onSubmit).toHaveBeenCalledWith({
      families: ['sleep'],
      reason_category: 'factory_reset',
      evidence_ref: 'ticket-42',
    });
  });

  it('submits families=null when 全部专项 is selected', async () => {
    const onSubmit = vi.fn();
    render(<DeviceReturnOverrideDialog {...baseProps} onSubmit={onSubmit} />);
    await userEvent.click(screen.getByLabelText('全部专项'));
    await userEvent.selectOptions(screen.getByLabelText('原因类别'), 'manual_verified');
    await userEvent.type(screen.getByLabelText('证据引用'), 'manual check');
    await userEvent.click(screen.getByTestId('return-override-submit'));
    expect(onSubmit).toHaveBeenCalledWith({
      families: null,
      reason_category: 'manual_verified',
      evidence_ref: 'manual check',
    });
  });

  it('shows closed count, lease release and remaining count after submission', () => {
    render(
      <DeviceReturnOverrideDialog
        {...baseProps}
        result={{
          device_id: 7,
          families: ['power-cycle'],
          closed_count: 3,
          debt_lease_released: false,
          open_debt_count: 2,
        }}
      />,
    );
    const result = screen.getByTestId('return-override-result');
    expect(result).toHaveTextContent('已关闭 3 条欠账');
    expect(result).toHaveTextContent('未释放欠账租约');
    expect(result).toHaveTextContent('剩余 2 条');
  });
});
