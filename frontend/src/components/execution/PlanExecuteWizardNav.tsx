import { Check, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

export const WIZARD_STEPS = [
  { title: '计划配置', description: '选择并核对测试计划' },
  { title: '样机选择', description: '先定位节点，再选择设备' },
  { title: '数量与版本确认', description: '核对节点分布和版本一致性' },
  { title: '执行前确认', description: '前置项、参数与最终预检' },
];

export function PlanExecuteWizardNav({ currentStep, onStepChange }: { currentStep: number; onStepChange: (step: number) => void }) {
  return <nav className="mb-4 rounded-xl border bg-card p-3" aria-label="执行配置进度"><div className="grid gap-2 md:grid-cols-4">{WIZARD_STEPS.map((step, index) => <button key={step.title} type="button" onClick={() => onStepChange(index)} aria-current={currentStep === index ? 'step' : undefined} className={cn('relative rounded-lg border px-3 py-3 text-left transition-colors', currentStep === index ? 'border-primary bg-primary/10' : index < currentStep ? 'border-success/40 bg-success/5' : 'border-transparent bg-muted/30 hover:bg-accent')}><div className="flex items-center gap-2"><span className={cn('flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold', currentStep === index ? 'bg-primary text-primary-foreground' : index < currentStep ? 'bg-success text-success-foreground' : 'bg-muted-foreground/20')}>{index < currentStep ? <Check className="h-3.5 w-3.5" /> : index + 1}</span><span className="text-sm font-medium">{step.title}</span></div><div className={cn('mt-1 pl-8 text-xs', TEXT.subtitle)}>{step.description}</div>{index < WIZARD_STEPS.length - 1 && <ChevronRight className="absolute -right-3 top-1/2 z-10 hidden h-4 w-4 -translate-y-1/2 text-muted-foreground md:block" />}</button>)}</div></nav>;
}
