import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ToastProvider, useToast } from './toast';

// Mock the cn utility
vi.mock('@/lib/utils', () => ({
  cn: (...classes: any[]) => classes.filter(Boolean).join(' '),
}));

// Helper component to trigger toasts from tests
function ToastTrigger() {
  const toast = useToast();
  return (
    <div>
      <button onClick={() => toast.success('Success message')}>Show Success</button>
      <button onClick={() => toast.error('Error message')}>Show Error</button>
      <button onClick={() => toast.info('Info message')}>Show Info</button>
    </div>
  );
}

describe('ToastProvider', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders children', () => {
    render(
      <ToastProvider>
        <div>Child Content</div>
      </ToastProvider>
    );
    expect(screen.getByText('Child Content')).toBeInTheDocument();
  });

  it('shows success toast when triggered', () => {
    render(
      <ToastProvider>
        <ToastTrigger />
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('Show Success'));
    expect(screen.getByText('Success message')).toBeInTheDocument();
  });

  it('shows error toast when triggered', () => {
    render(
      <ToastProvider>
        <ToastTrigger />
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('Show Error'));
    expect(screen.getByText('Error message')).toBeInTheDocument();
  });

  it('shows info toast when triggered', () => {
    render(
      <ToastProvider>
        <ToastTrigger />
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('Show Info'));
    expect(screen.getByText('Info message')).toBeInTheDocument();
  });

  it('auto-dismisses toast after 4 seconds', () => {
    render(
      <ToastProvider>
        <ToastTrigger />
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('Show Success'));
    expect(screen.getByText('Success message')).toBeInTheDocument();

    // Advance past the 4-second auto-dismiss timer
    act(() => {
      vi.advanceTimersByTime(4100);
    });

    expect(screen.queryByText('Success message')).not.toBeInTheDocument();
  });

  it('dismisses toast on close button click', () => {
    render(
      <ToastProvider>
        <ToastTrigger />
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('Show Success'));
    expect(screen.getByText('Success message')).toBeInTheDocument();

    // Find and click the dismiss button (X icon button)
    const dismissButton = screen.getByText('Success message')
      .closest('div')
      ?.querySelector('button');
    expect(dismissButton).toBeTruthy();
    fireEvent.click(dismissButton!);

    expect(screen.queryByText('Success message')).not.toBeInTheDocument();
  });

  it('can show multiple toasts simultaneously', () => {
    render(
      <ToastProvider>
        <ToastTrigger />
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('Show Success'));
    fireEvent.click(screen.getByText('Show Error'));
    fireEvent.click(screen.getByText('Show Info'));

    expect(screen.getByText('Success message')).toBeInTheDocument();
    expect(screen.getByText('Error message')).toBeInTheDocument();
    expect(screen.getByText('Info message')).toBeInTheDocument();
  });

  it('throws error when useToast is used outside provider', () => {
    function Orphan() {
      useToast();
      return null;
    }

    // Suppress console.error for expected error
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<Orphan />)).toThrow('useToast must be used within ToastProvider');
    spy.mockRestore();
  });
});
