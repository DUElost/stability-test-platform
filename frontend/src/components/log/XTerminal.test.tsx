import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React, { createRef } from 'react';

// Mock terminal instance methods
const mockWriteln = vi.fn();
const mockClear = vi.fn();
const mockScrollToBottom = vi.fn();
const mockDispose = vi.fn();
const mockLoadAddon = vi.fn();
const mockOpen = vi.fn();

// Mock xterm modules before importing the component - use class syntax for constructors
vi.mock('@xterm/xterm', () => ({
  Terminal: class MockTerminal {
    open = mockOpen;
    writeln = mockWriteln;
    clear = mockClear;
    scrollToBottom = mockScrollToBottom;
    dispose = mockDispose;
    loadAddon = mockLoadAddon;
  },
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class MockFitAddon {
    fit = vi.fn();
    dispose = vi.fn();
  },
}));

vi.mock('@xterm/addon-search', () => ({
  SearchAddon: class MockSearchAddon {
    findNext = vi.fn();
    findPrevious = vi.fn();
    clearDecorations = vi.fn();
    dispose = vi.fn();
  },
}));

vi.mock('@xterm/addon-web-links', () => ({
  WebLinksAddon: class MockWebLinksAddon {
    dispose = vi.fn();
  },
}));

// Import after mocks are set up
import { XTerminal, type XTerminalHandle, disposeAllTerminals } from './XTerminal';

describe('XTerminal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    disposeAllTerminals();
  });

  afterEach(() => {
    disposeAllTerminals();
  });

  it('renders the terminal container with toolbar', () => {
    render(<XTerminal poolKey="test-1" />);
    expect(screen.getByText('Terminal')).toBeDefined();
  });

  it('shows search and download buttons in toolbar', () => {
    render(<XTerminal poolKey="test-2" />);
    expect(screen.getByTitle('Search (Ctrl+F)')).toBeDefined();
    expect(screen.getByTitle('Download Log')).toBeDefined();
  });

  it('toggles search bar on search button click', () => {
    render(<XTerminal poolKey="test-3" />);
    const searchButton = screen.getByTitle('Search (Ctrl+F)');

    // Search bar should not be visible initially
    expect(screen.queryByPlaceholderText('Search...')).toBeNull();

    // Click search button
    fireEvent.click(searchButton);
    expect(screen.getByPlaceholderText('Search...')).toBeDefined();

    // Close search
    const closeButton = screen.getByTitle('Close search');
    fireEvent.click(closeButton);
    expect(screen.queryByPlaceholderText('Search...')).toBeNull();
  });

  it('exposes imperative handle methods', () => {
    const ref = createRef<XTerminalHandle>();
    render(<XTerminal ref={ref} poolKey="test-4" />);

    expect(ref.current).not.toBeNull();
    expect(ref.current!.writeLine).toBeDefined();
    expect(ref.current!.writeLines).toBeDefined();
    expect(ref.current!.clear).toBeDefined();
    expect(ref.current!.scrollToBottom).toBeDefined();
  });

  it('writeLine calls terminal.writeln with formatted output', () => {
    const ref = createRef<XTerminalHandle>();
    render(<XTerminal ref={ref} poolKey="test-5" />);

    act(() => {
      ref.current!.writeLine('test message', 'ERROR');
    });

    expect(mockWriteln).toHaveBeenCalled();
    const written = mockWriteln.mock.calls[0][0];
    // Should contain ANSI red color code for ERROR level
    expect(written).toContain('\x1b[31m');
    expect(written).toContain('test message');
  });

  it('writeLines writes multiple lines', () => {
    const ref = createRef<XTerminalHandle>();
    render(<XTerminal ref={ref} poolKey="test-6" />);

    act(() => {
      ref.current!.writeLines([
        { msg: 'line 1', level: 'INFO' },
        { msg: 'line 2', level: 'WARN' },
        { msg: 'line 3', level: 'ERROR' },
      ]);
    });

    expect(mockWriteln).toHaveBeenCalledTimes(3);
  });

  it('clear resets the terminal', () => {
    const ref = createRef<XTerminalHandle>();
    render(<XTerminal ref={ref} poolKey="test-7" />);

    act(() => {
      ref.current!.writeLine('before clear');
      ref.current!.clear();
    });

    expect(mockClear).toHaveBeenCalled();
  });

  it('highlights FATAL/CRASH keywords with bold red', () => {
    const ref = createRef<XTerminalHandle>();
    render(<XTerminal ref={ref} poolKey="test-8" />);

    act(() => {
      ref.current!.writeLine('Process FATAL exception occurred');
    });

    const written = mockWriteln.mock.calls[0][0];
    // Should contain bold-red ANSI for FATAL
    expect(written).toContain('\x1b[1;31m');
    expect(written).toContain('FATAL');
  });

  it('highlights ANR keyword with bold yellow', () => {
    const ref = createRef<XTerminalHandle>();
    render(<XTerminal ref={ref} poolKey="test-9" />);

    act(() => {
      ref.current!.writeLine('Application ANR detected');
    });

    const written = mockWriteln.mock.calls[0][0];
    // Should contain bold-yellow ANSI for ANR
    expect(written).toContain('\x1b[1;33m');
    expect(written).toContain('ANR');
  });

  it('calls onReady callback after mount', () => {
    const onReady = vi.fn();
    render(<XTerminal poolKey="test-10" onReady={onReady} />);
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it('opens terminal on container element', () => {
    render(<XTerminal poolKey="test-11" />);
    expect(mockOpen).toHaveBeenCalled();
  });

  it('loads all three addons', () => {
    render(<XTerminal poolKey="test-12" />);
    // FitAddon, SearchAddon, WebLinksAddon
    expect(mockLoadAddon).toHaveBeenCalledTimes(3);
  });
});
