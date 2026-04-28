import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import axios from 'axios';

// We need to test the module-level interceptors, so we'll import after mocking
vi.mock('axios', () => {
  const interceptors = {
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  };
  const instance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors,
  };
  return {
    default: {
      create: vi.fn(() => instance),
      post: vi.fn(), // for refresh token call
    },
  };
});

describe('api module', () => {
  let requestFulfilled: (config: any) => any;
  let responseRejected: (error: any) => any;

  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorage.clear();
    // Re-import to trigger interceptor registration
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('creates axios instance with correct baseURL', async () => {
    await import('./api');
    expect(axios.create).toHaveBeenCalledWith(
      expect.objectContaining({
        baseURL: '/api/v1',
        headers: { 'Content-Type': 'application/json' },
      })
    );
  });

  it('registers request and response interceptors', async () => {
    await import('./api');
    const instance = (axios.create as any).mock.results[0].value;
    expect(instance.interceptors.request.use).toHaveBeenCalled();
    expect(instance.interceptors.response.use).toHaveBeenCalled();
  });

  describe('request interceptor', () => {
    beforeEach(async () => {
      await import('./api');
      const instance = (axios.create as any).mock.results[0].value;
      requestFulfilled = instance.interceptors.request.use.mock.calls[0][0];
    });

    it('attaches Bearer token from localStorage', () => {
      localStorage.setItem('access_token', 'test-jwt-token');
      const config = { headers: {} as any, method: 'get', url: '/hosts' };
      const result = requestFulfilled(config);
      expect(result.headers.Authorization).toBe('Bearer test-jwt-token');
    });

    it('does not attach Authorization when no token exists', () => {
      const config = { headers: {} as any, method: 'get', url: '/hosts' };
      const result = requestFulfilled(config);
      expect(result.headers.Authorization).toBeUndefined();
    });
  });

  describe('response interceptor - 401 handling', () => {
    beforeEach(async () => {
      await import('./api');
      const instance = (axios.create as any).mock.results[0].value;
      responseRejected = instance.interceptors.response.use.mock.calls[0][1];
    });

    it('clears tokens and redirects on 401 without refresh_token', async () => {
      localStorage.setItem('access_token', 'expired');
      const error = { response: { status: 401 }, config: {} };

      // window.location.href is not easily testable in jsdom, but we can verify localStorage is cleared
      const originalHref = window.location.href;
      Object.defineProperty(window, 'location', {
        writable: true,
        value: { href: originalHref },
      });

      try {
        await responseRejected(error);
      } catch {
        // expected rejection
      }

      expect(localStorage.getItem('access_token')).toBeNull();
      expect(localStorage.getItem('refresh_token')).toBeNull();
    });

    it('attempts token refresh on 401 when refresh_token exists', async () => {
      localStorage.setItem('access_token', 'expired');
      localStorage.setItem('refresh_token', 'valid-refresh');

      const newTokens = {
        access_token: 'new-access',
        refresh_token: 'new-refresh',
      };
      (axios.post as any).mockResolvedValueOnce({ data: newTokens });

      const instance = (axios.create as any).mock.results[0].value;
      instance.mockResolvedValueOnce?.({ data: 'retried' });

      const error = {
        response: { status: 401 },
        config: { headers: {} as any, __retry: undefined },
      };

      try {
        await responseRejected(error);
      } catch {
        // may reject if instance call is not properly mocked
      }

      // Verify refresh was attempted
      expect(axios.post).toHaveBeenCalledWith('/api/v1/auth/refresh', {
        refresh_token: 'valid-refresh',
      });
    });

    it('does not retry if __retry flag is already set', async () => {
      localStorage.setItem('refresh_token', 'valid-refresh');

      const error = {
        response: { status: 401 },
        config: { headers: {}, __retry: true },
      };

      Object.defineProperty(window, 'location', {
        writable: true,
        value: { href: '' },
      });

      try {
        await responseRejected(error);
      } catch {
        // expected
      }

      expect(axios.post).not.toHaveBeenCalled();
    });
  });

  describe('api namespace methods', () => {
    it('exports hosts, devices, tasks, and other namespaces', async () => {
      const { api } = await import('./api');
      expect(api.hosts).toBeDefined();
      expect(api.hosts.list).toBeInstanceOf(Function);
      expect(api.hosts.create).toBeInstanceOf(Function);
      expect(api.hosts.update).toBeInstanceOf(Function);
      expect(api.devices).toBeDefined();
      expect(api.devices.list).toBeInstanceOf(Function);
      expect(api.orchestration).toBeDefined();
      expect(api.orchestration.list).toBeInstanceOf(Function);
      expect(api.orchestration.delete).toBeInstanceOf(Function);
      expect(api.execution).toBeDefined();
      expect(api.execution.listJobs).toBeInstanceOf(Function);
      expect(api.auth).toBeDefined();
      expect(api.auth.login).toBeInstanceOf(Function);
      expect(api.results).toBeDefined();
      expect(api.stats).toBeDefined();
      expect(api.notifications).toBeDefined();
      expect(api.schedules).toBeDefined();
      expect(api.templates).toBeDefined();
      expect(api.audit).toBeDefined();
      expect(api.scriptSequences).toBeDefined();
      expect(api.scriptSequences.list).toBeInstanceOf(Function);
      expect(api.scriptExecutions).toBeDefined();
      expect(api.scriptExecutions.create).toBeInstanceOf(Function);
      expect(api.scriptExecutions.get).toBeInstanceOf(Function);
    });
  });
});
