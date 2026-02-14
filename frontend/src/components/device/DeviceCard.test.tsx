import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DeviceCard, Device } from './DeviceCard';

const mockDevice: Device = {
  serial: 'SN123456',
  model: 'Pixel 6',
  status: 'idle',
  battery_level: 85,
  temperature: 32,
  network_latency: 50,
};

describe('DeviceCard', () => {
  it('renders basic device information', () => {
    render(<DeviceCard device={mockDevice} />);
    expect(screen.getByText('Pixel 6')).toBeInTheDocument();
    expect(screen.getByText('SN123456')).toBeInTheDocument();
    expect(screen.getByText(/Idle/i)).toBeInTheDocument();
  });

  it('renders different statuses with correct labels', () => {
    const { rerender } = render(<DeviceCard device={{ ...mockDevice, status: 'testing', current_task: 'Monkey Test' }} />);
    expect(screen.getByText(/Testing/i)).toBeInTheDocument();
    expect(screen.getByText('Monkey Test')).toBeInTheDocument();

    rerender(<DeviceCard device={{ ...mockDevice, status: 'error' }} />);
    expect(screen.getByText(/Error/i)).toBeInTheDocument();

    rerender(<DeviceCard device={{ ...mockDevice, status: 'offline' }} />);
    expect(screen.getByText(/Offline/i)).toBeInTheDocument();
  });

  it('shows low battery warning when level < 20%', () => {
    render(<DeviceCard device={{ ...mockDevice, battery_level: 15 }} />);
    const batteryText = screen.getByText('15%');
    expect(batteryText).toHaveClass('text-destructive');
  });

  it('shows high temperature warning when temp > 40°C', () => {
    render(<DeviceCard device={{ ...mockDevice, temperature: 42 }} />);
    const tempText = screen.getByText('42°C');
    expect(tempText).toHaveClass('text-destructive');
  });

  it('shows high temperature alert icon when temp > 45°C', () => {
    render(<DeviceCard device={{ ...mockDevice, temperature: 46 }} />);
    const tempContainer = screen.getByText('46°C').parentElement;
    expect(tempContainer?.querySelector('svg')).toBeInTheDocument();
  });

  it('renders network latency status correctly', () => {
    const { rerender } = render(<DeviceCard device={{ ...mockDevice, network_latency: 50 }} />);
    expect(screen.getByText(/online/i)).toBeInTheDocument();

    rerender(<DeviceCard device={{ ...mockDevice, network_latency: 250 }} />);
    expect(screen.getByText(/warning/i)).toBeInTheDocument();

    rerender(<DeviceCard device={{ ...mockDevice, network_latency: null }} />);
    expect(screen.getByText(/offline/i)).toBeInTheDocument();
  });

  it('calls onClick handler when clicked', () => {
    const handleClick = vi.fn();
    render(<DeviceCard device={mockDevice} onClick={handleClick} />);

    fireEvent.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalledWith(mockDevice);
  });

  it('calls onClick handler on Enter or Space key press', () => {
    const handleClick = vi.fn();
    render(<DeviceCard device={mockDevice} onClick={handleClick} />);

    const card = screen.getByRole('button');

    fireEvent.keyDown(card, { key: 'Enter', code: 'Enter' });
    expect(handleClick).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(card, { key: ' ', code: 'Space' });
    expect(handleClick).toHaveBeenCalledTimes(2);
  });

  it('displays host name when provided', () => {
    render(<DeviceCard device={{ ...mockDevice, host_id: 1, host_name: 'Worker-Node-01' }} />);
    expect(screen.getByText('Worker-Node-01')).toBeInTheDocument();
  });

  it('displays host ID fallback when host name is missing', () => {
    render(<DeviceCard device={{ ...mockDevice, host_id: 42, host_name: undefined }} />);
    expect(screen.getByText('Host #42')).toBeInTheDocument();
  });

  it('does not display battery/temp/network when offline', () => {
    render(<DeviceCard device={{ ...mockDevice, status: 'offline' }} />);
    expect(screen.queryByText(/Battery/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Temp/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Network/i)).not.toBeInTheDocument();
  });

  it('accessibility: has correct aria-label and role', () => {
    const handleClick = vi.fn();
    render(<DeviceCard device={mockDevice} onClick={handleClick} />);

    const card = screen.getByRole('button');
    expect(card).toHaveAttribute('aria-label', 'Device Pixel 6 - Idle');
    expect(card).toHaveAttribute('tabIndex', '0');
  });
});
