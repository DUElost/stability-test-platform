import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { BatchEditDeviceTagsDialog } from './BatchEditDeviceTagsDialog';

describe('BatchEditDeviceTagsDialog', () => {
  it('normalizes tags and submits the selected operation', () => {
    const onSubmit = vi.fn();
    render(
      <BatchEditDeviceTagsDialog
        isOpen
        selectedCount={3}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.change(screen.getByLabelText('标签'), {
      target: { value: 'shanghai, regression, shanghai' },
    });
    fireEvent.click(screen.getByRole('button', { name: '确认更新' }));

    expect(onSubmit).toHaveBeenCalledWith('add', ['shanghai', 'regression']);
  });

  it('requires tags for add and remove operations', () => {
    render(
      <BatchEditDeviceTagsDialog
        isOpen
        selectedCount={1}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '确认更新' }));
    expect(screen.getByText('请输入至少一个标签')).toBeInTheDocument();
  });
});
