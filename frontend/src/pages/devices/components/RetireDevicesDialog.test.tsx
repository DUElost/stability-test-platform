import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RetireDevicesDialog } from './RetireDevicesDialog';

type DialogProps = Parameters<typeof RetireDevicesDialog>[0];

function renderDialog(overrides: Partial<DialogProps> = {}) {
  render(
    <RetireDevicesDialog
      isOpen
      mode="retire"
      selectedCount={3}
      onClose={vi.fn()}
      onSubmit={vi.fn()}
      {...overrides}
    />,
  );
}

// #3483：幂等跳过计数是「本次不会改态」的台数——retire 数已退役的，unretire 数未退役的，
// 两种 mode 的文案语义不同。组件只消费父层算好的 idempotentSkipCount。
describe('RetireDevicesDialog 幂等跳过计数（#3483）', () => {
  it('retire 模式显示已退役台数（原读数不变）', () => {
    renderDialog({ mode: 'retire', idempotentSkipCount: 2 });
    expect(screen.getByRole('dialog')).toHaveTextContent('其中 2 台已是退役态（幂等跳过）。');
  });

  it('retire 模式无幂等跳过时不出现提示', () => {
    renderDialog({ mode: 'retire', idempotentSkipCount: 0 });
    expect(screen.getByRole('dialog')).not.toHaveTextContent('幂等跳过');
  });

  it('unretire 模式全选已退役（跳过 0 台）不出现「并未退役（幂等跳过）」', () => {
    renderDialog({ mode: 'unretire', idempotentSkipCount: 0 });
    expect(screen.getByRole('dialog')).toHaveTextContent('将对选中的 3 台设备解除退役');
    expect(screen.getByRole('dialog')).not.toHaveTextContent('并未退役（幂等跳过）');
  });

  it('unretire 模式混入在役设备时提示实际跳过台数', () => {
    renderDialog({ mode: 'unretire', idempotentSkipCount: 2 });
    expect(screen.getByRole('dialog')).toHaveTextContent('其中 2 台并未退役（幂等跳过）。');
  });
});
