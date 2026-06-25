import { Card, CardContent } from '@/components/ui/card';
import { Wifi, Code2, ChevronRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { PageContainer, PageHeader } from '@/components/layout';

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
  return (
    <PageContainer width="default">
      <PageHeader title="环境资源" subtitle="管理测试所需的 WiFi 配置、脚本等环境资源" />

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <ResourceCard
          title="WiFi 资源池"
          description="配置和管理设备 WiFi 连接池"
          icon={Wifi}
          to="/wifi"
        />
        <ResourceCard
          title="脚本库"
          description="查看与管理可调用的测试脚本"
          icon={Code2}
          to="/script-management"
        />
      </div>
    </PageContainer>
  );
}
