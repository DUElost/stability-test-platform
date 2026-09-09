import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AddDeviceModal } from './AddDeviceModal';

function renderModal(onSubmit = vi.fn()) {
  render(
    <AddDeviceModal
      isOpen
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />,
  );
  return onSubmit;
}

async function fillAndSubmit(hostId: string) {
  fireEvent.change(screen.getByLabelText(/序列号/), {
    target: { value: 'SERIAL-1' },
  });
  if (hostId !== '') {
    fireEvent.change(screen.getByLabelText(/主机 ID/), {
      target: { value: hostId },
    });
  }
  fireEvent.click(screen.getByRole('button', { name: /添加设备/ }));
}

describe('AddDeviceModal', () => {
  it('#953: submits string host_id verbatim (no Number() coercion)', async () => {
    const onSubmit = renderModal();
    await fillAndSubmit('192-168-1-200');

    expect(onSubmit).toHaveBeenCalledWith({
      serial: 'SERIAL-1',
      host_id: '192-168-1-200',
    });
  });

  it('#953: accepts host ids that are not numeric at all', async () => {
    const onSubmit = renderModal();
    await fillAndSubmit('host-a1');

    expect(onSubmit).toHaveBeenCalledWith({
      serial: 'SERIAL-1',
      host_id: 'host-a1',
    });
  });

  it('#953: empty host id omits the field', async () => {
    const onSubmit = renderModal();
    await fillAndSubmit('');

    expect(onSubmit).toHaveBeenCalledWith({ serial: 'SERIAL-1' });
  });

  it('#953: rejects whitespace-only / illegal characters in host id', async () => {
    const onSubmit = renderModal();
    await fillAndSubmit('bad host id!');

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/主机 ID 格式不合法/)).toBeInTheDocument();
  });
});
