import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Toaster } from './Toaster';
import { useToast } from '@/hooks/useToast';

function TestButton({ label, onClick }: { label: string; onClick: () => void }) {
  return <button onClick={onClick}>{label}</button>;
}

function SuccessComponent() {
  const toast = useToast();
  return <TestButton label="success" onClick={() => toast.success('Saved')} />;
}

describe('useToast', () => {
  it('shows success toast', async () => {
    render(
      <>
        <SuccessComponent />
        <Toaster />
      </>,
    );
    screen.getByText('success').click();
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument());
  });
});
