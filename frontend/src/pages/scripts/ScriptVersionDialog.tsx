import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useToast } from '@/hooks/useToast';
import { api, type ScriptEntry } from '@/utils/api';
import { Tag, AlertCircle } from 'lucide-react';
import { ALERT_BANNER, FORM, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

interface Props {
  open: boolean;
  script: ScriptEntry | null;
  onClose: () => void;
  onCreated: () => void;
}

export default function ScriptVersionDialog({ open, script, onClose, onCreated }: Props) {
  const toast = useToast();
  const [version, setVersion] = useState('');
  const [nfsPath, setNfsPath] = useState('');
  const [contentSha256, setContentSha256] = useState('');
  const [defaultParamsText, setDefaultParamsText] = useState('');
  const [paramSchemaText, setParamSchemaText] = useState('');
  const [description, setDescription] = useState('');
  const [parseError, setParseError] = useState('');

  // React 官方"adjust state when prop changes"模式：render 期对比 prev 快照并重置表单。
  // resetKey 为 `${open}|${script.id}` 稳定字符串，无引用比较问题。
  const resetKey = `${open}|${script?.id ?? ''}`;
  const [prevResetKey, setPrevResetKey] = useState(resetKey);
  if (prevResetKey !== resetKey) {
    setPrevResetKey(resetKey);
    if (open && script) {
      setVersion('');
      setNfsPath('');
      setContentSha256('');
      setDefaultParamsText('');
      setParamSchemaText('');
      setDescription('');
      setParseError('');
    }
  }

  if (!open || !script) return null;

  // #1026：SHA 与路径契约 —— 前端先拦一道，避免创建后必然校验失败的「必败版本」。
  // 后端 ScriptVersionCreate 有同型校验（64 位 hex + v{version} 目录一致）。
  const versionTrimmed = version.trim();
  const shaTrimmed = contentSha256.trim();
  const pathTrimmed = nfsPath.trim();
  const shaValid = /^[0-9a-fA-F]{64}$/.test(shaTrimmed);
  const pathValid =
    pathTrimmed !== '' && pathTrimmed.includes(`/v${versionTrimmed}/`);
  const canSubmit = versionTrimmed !== '' && shaValid && pathValid;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setParseError('');

    let defaultParams: Record<string, unknown> = {};
    let paramSchema: Record<string, unknown> = {};

    try {
      if (defaultParamsText.trim()) defaultParams = JSON.parse(defaultParamsText);
    } catch {
      setParseError('default_params JSON 格式无效');
      return;
    }

    try {
      if (paramSchemaText.trim()) paramSchema = JSON.parse(paramSchemaText);
    } catch {
      setParseError('param_schema JSON 格式无效');
      return;
    }

    try {
      await api.scripts.createVersion(script.name, {
        version: versionTrimmed,
        nfs_path: pathTrimmed,
        content_sha256: shaTrimmed,
        param_schema: paramSchema,
        default_params: defaultParams,
        description: description.trim() || undefined,
      });
      toast.success(`版本 ${versionTrimmed} 已创建`);
      onCreated();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '创建版本失败';
      toast.error(msg);
    }
  };

  return (
    <Dialog open={open && !!script} onOpenChange={(isOpen) => { if (!isOpen) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Tag className={cn('h-5 w-5', TEXT.subtitle)} />
            新建脚本版本 — {script!.name}
          </DialogTitle>
        </DialogHeader>

        <div className={cn('mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-xs', ALERT_BANNER.warning)}>
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          <span>
            修改 default_params 必须创建新的脚本版本。当前版本{' '}
            <code className="font-mono">{script!.version}</code> 的默认参数保持不变。
          </span>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label htmlFor="sv-version" className={FORM.label}>新版本 *</label>
            <input
              id="sv-version"
              type="text"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              required
              className={FORM.input}
              placeholder="如 2.0.0"
            />
          </div>
          <div>
            <label htmlFor="sv-description" className={FORM.label}>描述</label>
            <input
              id="sv-description"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className={FORM.input}
              placeholder="此版本的变更说明…"
            />
          </div>
          <div>
            <label htmlFor="sv-nfs-path" className={FORM.label}>NFS 路径 *</label>
            <input
              id="sv-nfs-path"
              type="text"
              value={nfsPath}
              onChange={(e) => setNfsPath(e.target.value)}
              className={FORM.input}
              placeholder={`/scripts/${script.name}/v2.0.0/${script.name}.py`}
            />
            {versionTrimmed !== '' && !pathValid && (
              <p className={FORM.error}>
                路径必须落在新版本的版本目录 v{versionTrimmed}/ 下（不能复用旧版本路径，
                否则扫描会判磁盘缺失并停用）
              </p>
            )}
          </div>
          <div>
            <label htmlFor="sv-sha256" className={FORM.label}>Content SHA256 *</label>
            <input
              id="sv-sha256"
              type="text"
              value={contentSha256}
              onChange={(e) => setContentSha256(e.target.value)}
              className={cn(FORM.input, 'font-mono')}
              placeholder="64位 hex..."
            />
            {shaTrimmed !== '' && !shaValid && (
              <p className={FORM.error}>SHA256 必须是 64 位 hex</p>
            )}
          </div>
          <div>
            <label htmlFor="sv-default-params" className={FORM.label}>
              default_params (JSON) *
              <span className={cn('ml-1 font-normal', TEXT.subtitle)}>— 修改此字段即为新建版本</span>
            </label>
            <textarea
              id="sv-default-params"
              value={defaultParamsText}
              onChange={(e) => setDefaultParamsText(e.target.value)}
              rows={4}
              className={FORM.textarea}
              placeholder='{"timeout": 30, "retry": 2}'
            />
          </div>
          <div>
            <label htmlFor="sv-param-schema" className={FORM.label}>param_schema (JSON)</label>
            <textarea
              id="sv-param-schema"
              value={paramSchemaText}
              onChange={(e) => setParamSchemaText(e.target.value)}
              rows={3}
              className={FORM.textarea}
              placeholder='{"timeout": {"type": "integer"}}'
            />
          </div>

          {parseError && <p className={FORM.error}>{parseError}</p>}

          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              创建版本
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
