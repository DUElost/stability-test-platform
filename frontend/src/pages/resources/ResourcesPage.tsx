import { Navigate } from 'react-router-dom';

/** 旧「环境资源」hub 已拆入侧边栏直达项；保留路由以兼容书签。 */
export default function ResourcesPage() {
  return <Navigate to="/wifi" replace />;
}
