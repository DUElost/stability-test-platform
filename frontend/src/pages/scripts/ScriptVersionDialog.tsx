import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast';
import { api, type ScriptEntry } from '@/utils/api';
import { Tag, AlertCircle } from 'lucide-react';
import { ALERT_BANNER, FORM, MODAL, TEXT } from '@/design-system';
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

  useEffect(() => {
    if (!open || !script) return;
    setVersion('');
    setNfsPath('');
    setContentSha256('');
    setDefaultParamsText('');
    setParamSchemaText('');
    setDescription('');
    setParseError('');
  }, [script, open]);

  if (!open || !script) return null;

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
        version: version.trim(),
        nfs_path: nfsPath.trim() || script.nfs_path,
        content_sha256: contentSha256.trim(),
        param_schema: paramSchema,
        default_params: defaultParams,
        description: description.trim() || undefined,
      });
      toast.success(`版本 ${version} 已创建`);
      onCreated();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '创建版本失败';
      toast.error(msg);
    }
  };

  return (
    <div className={MODAL.overlay} onClick={onClose}>
      <div className={MODAL.panelLg} onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center gap-2">
          <Tag className={cn('h-5 w-5', TEXT.subtitle)} />
          <h2 className={cn('text-lg font-semibold', TEXT.heading)}>新建脚本版本 — {script.name}</h2>
        </div>

        <div className={cn('mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-xs', ALERT_BANNER.warning)}>
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          <span>
            修改 default_params 必须创建新的脚本版本。当前版本{' '}
            <code className="font-mono">{script.version}</code> 的默认参数保持不变。
          </span>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className={FORM.label}>新版本 *</label>
            <input
              type="text"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              required
              className={FORM.input}
              placeholder="如 2.0.0"
            />
          </div>
          <div>
            <label className={FORM.label}>描述</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className={FORM.input}
              placeholder="此版本的变更说明…"
            />
          </div>
          <div>
            <label className={FORM.label}>NFS 路径</label>
            <input
              type="text"
              value={nfsPath}
              onChange={(e) => setNfsPath(e.target.value)}
              className={FORM.input}
              placeholder={script.nfs_path || '/scripts/name/v2.0.0/main.py'}
            />
          </div>
          <div>
            <label className={FORM.label}>Content SHA256</label>
            <input
              type="text"
              value={contentSha256}
              onChange={(e) => setContentSha256(e.target.value)}
              className={cn(FORM.input, 'font-mono')}
              placeholder="64位 hex..."
            />
          </div>
          <div>
            <label className={FORM.label}>
              default_params (JSON) *
              <span className={cn('ml-1 font-normal', TEXT.subtitle)}>— 修改此字段即为新建版本</span>
            </label>
            <textarea
              value={defaultParamsText}
              onChange={(e) => setDefaultParamsText(e.target.value)}
              rows={4}
              className={FORM.textarea}
              placeholder='{"timeout": 30, "retry": 2}'
            />
          </div>
          <div>
            <label className={FORM.label}>param_schema (JSON)</label>
            <textarea
              value={paramSchemaText}
              onChange={(e) => setParamSchemaText(e.target.value)}
              rows={3}
              className={FORM.textarea}
              placeholder='{"timeout": {"type": "int"}}'
            />
          </div>

          {parseError && <p className={FORM.error}>{parseError}</p>}

          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={!version.trim()}>
              创建版本
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
