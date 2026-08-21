import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, ArrowLeft, ChevronRight, Code2, Play, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { STATUS_CHIP } from '@/design-system/tokens';

interface PlanEditHeaderFormLike {
  name: string;
  isNew: boolean;
  isDirty: boolean;
  saving: boolean;
  setShowJson: (v: boolean) => void;
  handleExecute: () => void;
  handleSave: () => Promise<unknown>;
}

/**
 * Plan 编辑页头（返回 + 面包屑 + 保存状态 chip + 操作），注入 AppShell 顶栏槽位。
 *
 * 页头是编辑页形态的规范用法（见 docs/design/2026-08-21-frontend-page-shell-spec.md
 * §2 页头三形态）：不再在页面内自绘头栏，避免与 AppShell 顶栏形成上下两条头栏。
 *
 * 依赖策略：显示值（name / isDirty / saving）直接列依赖，随输入重注入；
 * 回调经 formRef 取最新 —— usePlanEditForm 的回调是普通函数、引用不稳定，
 * 直接进依赖会在每次重注入时再次触发 effect，形成无限循环。
 */
export function usePlanEditHeaderSlot(form: PlanEditHeaderFormLike, ready: boolean) {
  const navigate = useNavigate();
  const { setHeaderSlot } = useHeaderSlot();
  const formRef = useRef(form);

  // 每次渲染后同步最新 form（渲染期写 ref 违反 react-hooks/refs）。
  // 无依赖数组 = 每次渲染后执行；本 effect 先声明，注入 effect 后执行，拿到的必是最新引用
  useEffect(() => {
    formRef.current = form;
  });

  useEffect(() => {
    // 加载中/错误分支不注入：与其余页面一致，顶栏留空；错误分支页内已有逃生口返回按钮
    if (!ready) return;
    setHeaderSlot(
      <div className="flex w-full min-w-0 items-center gap-3">
        <Button
          variant="ghost"
          size="sm"
          aria-label="返回 Plan 列表"
          className="h-8 px-2 text-muted-foreground"
          onClick={() => navigate('/orchestration/plans')}
        >
          <ArrowLeft className="w-4 h-4" />
        </Button>

        <div className="flex items-center gap-2 text-[13px] text-muted-foreground min-w-0">
          <span className="whitespace-nowrap">测试计划</span>
          <ChevronRight className="w-3.5 h-3.5 text-border shrink-0" />
          <strong className="text-foreground font-bold text-base truncate">
            {form.name || (form.isNew ? '新建 Plan' : '未命名 Plan')}
          </strong>
          {form.isDirty ? (
            <span
              className={`ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_CHIP.warning} border border-warning`}
            >
              <AlertCircle className="w-3 h-3" /> 未保存
            </span>
          ) : (
            <span
              className={`ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_CHIP.success} border border-success`}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-success" /> 已保存
            </span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground"
            onClick={() => formRef.current.setShowJson(true)}
          >
            <Code2 className="w-4 h-4 mr-1.5" />
            查看 JSON
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={() => formRef.current.handleExecute()}
            disabled={formRef.current.saving}
          >
            <Play className="w-4 h-4 mr-1.5" />
            发起测试
          </Button>
          <Button
            size="sm"
            onClick={() => void formRef.current.handleSave()}
            disabled={form.saving || !form.isDirty}
          >
            <Save className="w-4 h-4 mr-1.5" />
            {form.saving ? '保存中…' : form.isNew ? '创建' : '保存修改'}
          </Button>
        </div>
      </div>,
    );
    return () => setHeaderSlot(null);
  }, [ready, form.name, form.isNew, form.isDirty, form.saving, navigate, setHeaderSlot]);
}
