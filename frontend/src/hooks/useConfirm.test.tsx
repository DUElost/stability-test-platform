import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ConfirmProvider, useConfirm } from './useConfirm';

function Probe() {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      data-testid="double-confirm"
      onClick={() => {
        void (async () => {
          const first = confirm({ description: 'first' });
          const second = confirm({ description: 'second' });
          expect(await first).toBe(false);
          expect(await second).toBe(true);
        })();
      }}
    >
      run
    </button>
  );
}

describe('useConfirm', () => {
  it('resolves a superseded confirm with false when opened again', async () => {
    render(
      <ConfirmProvider>
        <Probe />
      </ConfirmProvider>,
    );

    fireEvent.click(screen.getByTestId('double-confirm'));
    expect(await screen.findByText('second')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    await waitFor(() => {
      expect(screen.queryByText('second')).not.toBeInTheDocument();
    });
  });
});
