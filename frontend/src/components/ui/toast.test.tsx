import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { Toaster } from './Toaster';
import { useToast } from '@/hooks/useToast';
import { ThemeProvider } from '@/contexts/ThemeContext';

function TestButton({ label, onClick }: { label: string; onClick: () => void }) {
  return <button onClick={onClick}>{label}</button>;
}

function SuccessComponent() {
  const toast = useToast();
  return <TestButton label="success" onClick={() => toast.success('Saved')} />;
}

describe('useToast', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
        onchange: null,
      })),
    );
  });

  it('shows success toast', async () => {
    render(
      <ThemeProvider>
        <SuccessComponent />
        <Toaster />
      </ThemeProvider>,
    );
    screen.getByText('success').click();
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument());
  });
});
