import { useState, useMemo } from 'react';



import { useNavigate } from 'react-router-dom';



import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';



import { Card, CardContent } from '@/components/ui/card';



import { Button } from '@/components/ui/button';



import { planKeys } from '@/utils/api/queryKeys';



import { Skeleton } from '@/components/ui/skeleton';



import { useToast } from '@/components/ui/toast';



import { api, type Plan } from '@/utils/api';



import { Plus, Edit, Trash2, Search, FileText, Play } from 'lucide-react';



import { PageContainer, PageHeader } from '@/components/layout';







export default function PlanListPage() {



  const navigate = useNavigate();



  const toast = useToast();



  const queryClient = useQueryClient();



  const [search, setSearch] = useState('');







  const { data: plans, isLoading } = useQuery({



    queryKey: planKeys.list(100),



    queryFn: () => api.plans.list(0, 100),



  });







  const deleteMutation = useMutation({



    mutationFn: (id: number) => api.plans.delete(id),



    onSuccess: () => {



      queryClient.invalidateQueries({ queryKey: planKeys.allLists() });



      toast.success('Plan 已删除');



    },



    onError: (err: any) => toast.error(err.message || '删除失败'),



  });







  const filtered = useMemo(() => {



    if (!plans) return [];



    const q = search.toLowerCase();



    return plans.filter(p =>



      !q || p.name.toLowerCase().includes(q) || (p.description || '').toLowerCase().includes(q)



    );



  }, [plans, search]);







  const handleDelete = async (plan: Plan) => {



    const ok = window.confirm(`确定删除 "${plan.name}"？此操作不可撤销。`);



    if (ok) deleteMutation.mutate(plan.id);



  };







  const stats = useMemo(() => ({



    total: plans?.length ?? 0,



    withSteps: plans?.filter(p => p.steps?.length > 0).length ?? 0,



    chained: plans?.filter(p => p.next_plan_id != null).length ?? 0,



  }), [plans]);







  return (



    <PageContainer className="max-w-5xl">



      <PageHeader title="Plan 编排" subtitle="基于 Plan-Step 模型管理测试编排，支持链接式 Plan 链" />







      {/* Stats */}







      <div className="grid grid-cols-3 gap-4">



        <Card>



          <CardContent className="py-4 text-center">



            <p className="text-2xl font-bold text-gray-900">{stats.total}</p>



            <p className="text-xs text-gray-500">Plan 总数</p>



          </CardContent>



        </Card>



        <Card>



          <CardContent className="py-4 text-center">



            <p className="text-2xl font-bold text-gray-900">{stats.withSteps}</p>



            <p className="text-xs text-gray-500">已配置步骤</p>



          </CardContent>



        </Card>



        <Card>



          <CardContent className="py-4 text-center">



            <p className="text-2xl font-bold text-gray-900">{stats.chained}</p>



            <p className="text-xs text-gray-500">链式 Plan</p>



          </CardContent>



        </Card>



      </div>







      {/* Search + Create */}







      <div className="flex items-center gap-3">



        <div className="relative flex-1">



          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />



          <input



            type="text" placeholder="搜索 Plan 名称或描述..." value={search}



            onChange={e => setSearch(e.target.value)}



            className="w-full pl-9 pr-4 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20"



          />



        </div>



        <Button onClick={() => navigate('/orchestration/plans/new')}>



          <Plus className="w-4 h-4 mr-1.5" /> 新建 Plan



        </Button>



      </div>







      {/* List */}







      {isLoading ? (



        <div className="space-y-3">



          <Skeleton className="h-20 w-full" />



          <Skeleton className="h-20 w-full" />



          <Skeleton className="h-20 w-full" />



        </div>



      ) : filtered.length === 0 ? (



        <Card>



          <CardContent className="py-12 text-center text-gray-400">



            <FileText className="w-10 h-10 mx-auto mb-3 text-gray-300" />



            <p className="text-sm">{search ? '没有匹配的 Plan' : '暂无 Plan，点击"新建 Plan"开始'}</p>



          </CardContent>



        </Card>



      ) : (



        <div className="space-y-3">



          {filtered.map(plan => (



            <Card key={plan.id} className="group hover:shadow-md transition-shadow">



              <CardContent className="py-4 flex items-center justify-between">



                <div className="min-w-0 flex-1">



                  <div className="flex items-center gap-2">



                    <h3 className="font-medium text-gray-900 truncate">{plan.name}</h3>



                    {plan.next_plan_id != null && (



                      <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">链式</span>



                    )}



                  </div>



                  {plan.description && (



                    <p className="text-sm text-gray-500 mt-0.5 truncate">{plan.description}</p>



                  )}



                  <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-400">



                    <span>{plan.steps?.length ?? 0} 步骤</span>



                    <span>阈值 {Math.round((plan.failure_threshold ?? 0.05) * 100)}%</span>



                    {plan.created_by && <span>创建者: {plan.created_by}</span>}



                    <span>更新于 {new Date(plan.updated_at).toLocaleDateString()}</span>



                  </div>



                </div>



                <div className="flex items-center gap-1 ml-4 opacity-0 group-hover:opacity-100 transition-opacity">



                  <Button variant="ghost" size="sm" onClick={() => navigate(`/execution/plan-execute?plan=${plan.id}`)} title="执行">



                    <Play className="w-4 h-4" />



                  </Button>



                  <Button variant="ghost" size="sm" onClick={() => navigate(`/orchestration/plans/${plan.id}`)} title="编辑">



                    <Edit className="w-4 h-4" />



                  </Button>



                  <Button variant="ghost" size="sm" onClick={() => handleDelete(plan)} className="text-red-500 hover:text-red-700" title="删除">



                    <Trash2 className="w-4 h-4" />



                  </Button>



                </div>



              </CardContent>



            </Card>



          ))}



        </div>



      )}



    </PageContainer>



  );



}



