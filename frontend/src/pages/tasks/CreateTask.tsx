import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CreateTaskForm } from '../../components/task/CreateTaskForm';
import { api } from '../../utils/api';
import { Device } from '../../components/device/DeviceCard';

const deviceStatusMap: Record<string, Device['status']> = {
  'ONLINE': 'idle',
  'BUSY': 'testing',
  'OFFLINE': 'offline',
  'ERROR': 'error'
};

function toComponentDevice(device: any): Device {
  return {
    id: device.id,
    serial: device.serial,
    model: device.model || 'Unknown',
    status: deviceStatusMap[device.status] || 'offline',
    battery_level: 0,
    temperature: 0,
  };
}

export default function CreateTask() {
  const navigate = useNavigate();

  const { data: devices } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list().then(res => res.data),
  });

  const handleSubmit = async (taskData: { type: string; deviceIds: number[]; config: Record<string, any> }) => {
    try {
      // Create a task for each selected device
      const promises = taskData.deviceIds.map((deviceId: number) =>
        api.tasks.create({
          name: `${taskData.type}-device-${deviceId}-${Date.now()}`,
          type: taskData.type,
          target_device_id: deviceId,
          params: taskData.config,
        })
      );

      await Promise.all(promises);
      navigate('/tasks');
    } catch (error) {
      console.error('Failed to create task:', error);
      alert('Failed to create task');
    }
  };

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Create New Task</h1>
        <p className="text-slate-500">Configure a new stability test task.</p>
      </div>

      <CreateTaskForm
        devices={devices ? devices.map(toComponentDevice) : []}
        onSubmit={handleSubmit}
      />
    </div>
  );
}
