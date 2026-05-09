import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast';
import { api, type ScriptEntry } from '@/utils/api';
import { Tag, AlertCircle } from 'lucide-react';

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

  // Reset form when dialog opens with a different script
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

    let defaultParams: Record<string, any> = {};
    let paramSchema: Record<string, any> = {};

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
    } catch (err: any) {
      toast.error(err.message || '创建版本失败');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-4">
          <Tag className="w-5 h-5 text-gray-500" />
          <h2 className="text-lg font-semibold">新建脚本版本 — {script.name}</h2>
        </div>

        <div className="mb-3 text-xs text-amber-600 bg-amber-50 px-3 py-2 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
          <span>修改 default_params 必须创建新的脚本版本。当前版本 <code className="font-mono">{script.version}</code> 的默认参数保持不变。</span>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">新版本 *</label>
            <input type="text" value={version} onChange={e => setVersion(e.target.value)} required
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20"
              placeholder="如 2.0.0" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
            <input type="text" value={description} onChange={e => setDescription(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20"
              placeholder="此版本的变更说明…" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">NFS 路径</label>
            <input type="text" value={nfsPath} onChange={e => setNfsPath(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20"
              placeholder={script.nfs_path || '/scripts/name/v2.0.0/main.py'} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Content SHA256</label>
            <input type="text" value={contentSha256} onChange={e => setContentSha256(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 font-mono"
              placeholder="64位 hex..." />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              default_params (JSON) *
              <span className="text-gray-400 font-normal ml-1">— 修改此字段即为新建版本</span>
            </label>
            <textarea value={defaultParamsText} onChange={e => setDefaultParamsText(e.target.value)} rows={4}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 font-mono"
              placeholder='{"timeout": 30, "retry": 2}' />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">param_schema (JSON)</label>
            <textarea value={paramSchemaText} onChange={e => setParamSchemaText(e.target.value)} rows={3}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 font-mono"
              placeholder='{"timeout": {"type": "int"}}' />
          </div>

          {parseError && <p className="text-sm text-red-600">{parseError}</p>}

          <div className="flex justify-end gap-2 mt-4">
            <Button type="button" variant="outline" onClick={onClose}>取消</Button>
            <Button type="submit" disabled={!version.trim()}>创建版本</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
