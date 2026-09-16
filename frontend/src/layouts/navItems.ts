import type { ElementType } from 'react';
import {
  AlertCircle,
  CalendarClock,
  Code2,
  FileBox,
  FolderKanban,
  HardDrive,
  Layers,
  LayoutDashboard,
  ListTodo,
  Rocket,
  Server,
  Smartphone,
  TestTube2,
  Wifi,
} from 'lucide-react';

export interface NavItem {
  path: string;
  label: string;
  icon: ElementType;
  /** 与 router AdminRoute 对齐，非 admin 不展示 */
  adminOnly?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}


export const navGroups: NavGroup[] = [
  {
    label: '工作区',
    items: [
      { path: '/', label: '仪表盘', icon: LayoutDashboard },
      { path: '/projects', label: '项目登记簿', icon: FolderKanban },
      { path: '/orchestration/plans', label: 'Plan 管理', icon: FileBox },
      { path: '/execution/plan-execute', label: '执行 Plan', icon: Rocket },
      { path: '/execution/plan-runs', label: '执行记录', icon: ListTodo },
    ],
  },
  {
    label: '分析报告',
    items: [
      { path: '/results', label: '测试结果', icon: TestTube2 },
      { path: '/issue-tracker', label: '问题追踪', icon: AlertCircle },
    ],
  },
  {
    // 中频：排查/维护期使用，默认折叠
    label: '资源',
    items: [
      { path: '/hosts', label: '主机集群', icon: Server },
      { path: '/devices', label: '物理设备', icon: Smartphone },
      { path: '/script-management', label: '脚本库', icon: Code2 },
      { path: '/test-suites', label: '用例套件', icon: Layers },
      { path: '/storage', label: '文件服务器', icon: HardDrive, adminOnly: true },
    ],
  },
  {
    // 低频长尾：有但不必显眼
    label: '更多功能',
    items: [
      { path: '/wifi', label: 'WiFi 资源池', icon: Wifi },
      { path: '/schedules', label: '定时调度', icon: CalendarClock },
    ],
  },
];
