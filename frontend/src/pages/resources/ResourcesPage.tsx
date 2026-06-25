import React from 'react';
import { Wifi, Code2, ChevronRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent } from '@/components/ui/card';
import { PageContainer, PageHeader } from '@/components/layout';
import { SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

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
        <div className={cn('flex items-center justify-center w-12 h-12 rounded-lg', SURFACE.subtle)}>
          <Icon className={cn('w-6 h-6', TEXT.subtitle)} />
        </div>
        <div className="flex-1">
          <h3 className={cn('font-medium', TEXT.heading)}>{title}</h3>
          <p className={cn('text-sm', TEXT.subtitle)}>{description}</p>
        </div>
        <ChevronRight className={cn('w-5 h-5', TEXT.subtle)} />
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
