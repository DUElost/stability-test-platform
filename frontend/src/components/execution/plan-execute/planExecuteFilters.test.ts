import { describe, expect, it } from 'vitest';
import {
  buildActiveFilterChips,
  clearActiveFilterChip,
  hasFilterQueryParams,
  parsePlanExecuteFilterParams,
  writeFilterParamsToSearch,
} from './planExecuteFilters';

describe('planExecuteFilters', () => {
  it('parses and writes filter query params round-trip', () => {
    const input = new URLSearchParams(
      'plan=7&devices=1,2&view=table&q=ABC&version=V104&model=ELA&host=h1&tags=回归,pool&ready=1',
    );
    const parsed = parsePlanExecuteFilterParams(input);
    expect(parsed).toEqual({
      q: 'ABC',
      version: 'V104',
      model: 'ELA',
      host: 'h1',
      tags: ['回归', 'pool'],
      readyOnly: true,
      view: 'table',
    });

    const written = writeFilterParamsToSearch(new URLSearchParams('plan=7&devices=1,2'), {
      ...parsed,
      q: '  ABC  ',
    });
    expect(written.get('plan')).toBe('7');
    expect(written.get('devices')).toBe('1,2');
    expect(written.get('q')).toBe('ABC');
    expect(written.get('version')).toBe('V104');
    expect(written.get('model')).toBe('ELA');
    expect(written.get('host')).toBe('h1');
    expect(written.get('tags')).toBe('回归,pool');
    expect(written.get('ready')).toBe('1');
    expect(written.get('view')).toBe('table');
  });

  it('omits default filter values from URL', () => {
    const written = writeFilterParamsToSearch(new URLSearchParams('plan=7'), {
      q: '',
      version: 'all',
      model: 'all',
      host: 'all',
      tags: [],
      readyOnly: false,
      view: 'matrix',
    });
    expect(written.get('plan')).toBe('7');
    expect(written.has('q')).toBe(false);
    expect(written.has('version')).toBe(false);
    expect(written.has('ready')).toBe(false);
    expect(written.get('view')).toBe('matrix');
  });

  it('detects filter query presence without treating view alone as filters', () => {
    expect(hasFilterQueryParams(new URLSearchParams('view=table'))).toBe(false);
    expect(hasFilterQueryParams(new URLSearchParams('ready=1'))).toBe(true);
    expect(hasFilterQueryParams(new URLSearchParams('q=x'))).toBe(true);
  });

  it('builds and clears active chips', () => {
    const chips = buildActiveFilterChips(
      {
        q: 's1',
        version: 'V1',
        model: 'ELA',
        host: 'h1',
        tags: ['回归'],
        readyOnly: true,
      },
      { hostLabel: '10.0.0.1' },
    );
    expect(chips.map((c) => c.label)).toEqual([
      '搜索:s1',
      '版本:V1',
      '型号:ELA',
      '节点:10.0.0.1',
      '标签:回归',
      '仅就绪',
    ]);

    const base = {
      q: 's1',
      version: 'V1',
      model: 'ELA',
      host: 'h1',
      tags: ['回归', 'pool'],
      readyOnly: true,
      view: 'matrix' as const,
    };
    expect(clearActiveFilterChip(base, 'q').q).toBe('');
    expect(clearActiveFilterChip(base, 'version').version).toBe('all');
    expect(clearActiveFilterChip(base, 'host').host).toBe('all');
    expect(clearActiveFilterChip(base, 'ready').readyOnly).toBe(false);
    expect(clearActiveFilterChip(base, 'tag:回归').tags).toEqual(['pool']);
  });
});
