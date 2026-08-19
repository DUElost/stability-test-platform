import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { Toaster } from './Toaster';
import { useToast, type ToastAPI } from '@/hooks/useToast';
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

  // 回归兜底（#314）：引用不稳定会让把它放进依赖数组的消费方
  // （如 SchedulesPage 的 loadAll）每次渲染重跑 effect、无限重发请求。
  it('returns the same reference across renders', () => {
    const refs: ToastAPI[] = [];
    const StabilityProbe = ({ refs }: { refs: ToastAPI[] }) => {
      refs.push(useToast());
      return null;
    };

    const { rerender } = render(
      <ThemeProvider>
        <StabilityProbe refs={refs} />
      </ThemeProvider>,
    );
    rerender(
      <ThemeProvider>
        <StabilityProbe refs={refs} />
      </ThemeProvider>,
    );
    rerender(
      <ThemeProvider>
        <StabilityProbe refs={refs} />
      </ThemeProvider>,
    );

    expect(refs.length).toBeGreaterThanOrEqual(3);
    expect(new Set(refs).size).toBe(1);
  });
});
