import React from 'react';

export interface SchemaField {
  type: 'string' | 'number' | 'boolean' | 'select';
  label: string;
  placeholder?: string;
  default?: any;
  required?: boolean;
  min?: number;
  max?: number;
  options?: { label: string; value: any }[];
  description?: string;
}

export type ParamSchema = Record<string, SchemaField>;

interface DynamicToolFormProps {
  schema: ParamSchema;
  values: Record<string, any>;
  onChange: (key: string, value: any) => void;
}

export const DynamicToolForm: React.FC<DynamicToolFormProps> = ({ schema, values, onChange }) => {
  const renderField = (key: string, field: SchemaField) => {
    const value = values[key] ?? field.default;

    switch (field.type) {
      case 'boolean':
        return (
          <div key={key} className="flex items-center gap-2 py-2">
            <input
              type="checkbox"
              id={key}
              checked={!!value}
              onChange={(e) => onChange(key, e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            <label htmlFor={key} className="text-sm font-medium text-slate-700 cursor-pointer">
              {field.label || key}
              {field.description && <span className="block text-xs text-slate-400 font-normal">{field.description}</span>}
            </label>
          </div>
        );

      case 'number':
        return (
          <div key={key} className="flex flex-col gap-1">
            <label htmlFor={key} className="text-xs font-medium text-slate-700">
              {field.label || key} {field.required && <span className="text-red-500">*</span>}
            </label>
            <input
              type="number"
              id={key}
              value={value ?? ''}
              min={field.min}
              max={field.max}
              required={field.required}
              placeholder={field.placeholder}
              onChange={(e) => onChange(key, e.target.value === '' ? undefined : Number(e.target.value))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
            />
            {field.description && <p className="text-[10px] text-slate-400">{field.description}</p>}
          </div>
        );

      case 'select':
        return (
          <div key={key} className="flex flex-col gap-1">
            <label htmlFor={key} className="text-xs font-medium text-slate-700">
              {field.label || key} {field.required && <span className="text-red-500">*</span>}
            </label>
            <select
              id={key}
              value={value ?? ''}
              required={field.required}
              onChange={(e) => onChange(key, e.target.value)}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none bg-white"
            >
              <option value="">Select an option</option>
              {field.options?.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            {field.description && <p className="text-[10px] text-slate-400">{field.description}</p>}
          </div>
        );

      case 'string':
      default:
        return (
          <div key={key} className="flex flex-col gap-1">
            <label htmlFor={key} className="text-xs font-medium text-slate-700">
              {field.label || key} {field.required && <span className="text-red-500">*</span>}
            </label>
            <input
              type="text"
              id={key}
              value={value ?? ''}
              required={field.required}
              placeholder={field.placeholder}
              onChange={(e) => onChange(key, e.target.value)}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
            />
            {field.description && <p className="text-[10px] text-slate-400">{field.description}</p>}
          </div>
        );
    }
  };

  if (!schema || Object.keys(schema).length === 0) {
    return (
      <div className="text-sm text-slate-400 italic">
        No configurable parameters for this tool.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {Object.entries(schema).map(([key, field]) => (
        <React.Fragment key={key}>
          {renderField(key, field)}
        </React.Fragment>
      ))}
    </div>
  );
};
