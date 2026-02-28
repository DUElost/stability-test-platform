import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Wifi, Wrench, HardDrive, ChevronRight } from 'lucide-react';
import { useNavigate, Link } from 'react-router-dom';

interface ResourceCardProps {
  title: string;
  description: string;
  icon: React.ElementType;
  to: string;
}

function ResourceCard({ title, description, icon: Icon, to }: ResourceCardProps) {
  const navigate = useNavigate();

  return (
    <Card
      className="cursor-pointer hover:shadow-md transition-shadow"
      onClick={() => navigate(to)}
    >
      <CardContent className="flex items-center gap-4 p-4">
        <div className="flex items-center justify-center w-12 h-12 rounded-lg bg-gray-100">
          <Icon className="w-6 h-6 text-gray-600" />
        </div>
        <div className="flex-1">
          <h3 className="font-medium text-gray-900">{title}</h3>
          <p className="text-sm text-gray-500">{description}</p>
        </div>
        <ChevronRight className="w-5 h-5 text-gray-400" />
      </CardContent>
    </Card>
  );
}

export default function ResourcesPage() {
  const [activeTab, setActiveTab] = useState<'overview' | 'wifi' | 'storage'>('overview');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">环境资源</h1>
        <p className="text-gray-500 mt-1">管理 WiFi 配置、存储工具等环境资源</p>
      </div>

      <div className="flex gap-2 border-b">
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'overview'
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
          onClick={() => setActiveTab('overview')}
        >
          概览
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'wifi'
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
          onClick={() => setActiveTab('wifi')}
        >
          WiFi 管理
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'storage'
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
          onClick={() => setActiveTab('storage')}
        >
          存储工具
        </button>
      </div>

      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <ResourceCard
            title="WiFi 管理"
            description="配置和管理设备 WiFi 连接"
            icon={Wifi}
            to="/wifi"
          />
          <ResourceCard
            title="存储工具"
            description="存储填充、清理等工具"
            icon={HardDrive}
            to="/tools"
          />
        </div>
      )}

      {activeTab === 'wifi' && (
        <Card>
          <CardHeader>
            <CardTitle>WiFi 管理</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-center py-8 text-gray-500">
              <Wifi className="w-12 h-12 mx-auto mb-4 text-gray-300" />
              <p>WiFi 管理功能</p>
              <p className="text-sm mt-2">
                <Link to="/wifi" className="text-blue-600 hover:underline">
                  跳转到 WiFi 管理页面
                </Link>
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {activeTab === 'storage' && (
        <Card>
          <CardHeader>
            <CardTitle>存储工具</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-center py-8 text-gray-500">
              <Wrench className="w-12 h-12 mx-auto mb-4 text-gray-300" />
              <p>存储工具功能</p>
              <p className="text-sm mt-2">
                <Link to="/tools" className="text-blue-600 hover:underline">
                  跳转到工具专项页面
                </Link>
              </p>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
