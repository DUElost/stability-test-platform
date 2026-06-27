import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import axios from 'axios';

const mocks = vi.hoisted(() => ({
  authFailureHandler: vi.fn(),
}));

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

  beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();
    // Re-import to trigger interceptor registration, then wire the auth-failure
    // handler that client.ts dispatches to on terminal 401.
    const mod = await import('./api');
    mod.registerAuthFailureHandler(mocks.authFailureHandler);
    const instance = (axios.create as any).mock.results[0].value;
    requestFulfilled = instance.interceptors.request.use.mock.calls[0]?.[0];
    responseRejected = instance.interceptors.response.use.mock.calls[0]?.[1];
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates axios instance with correct baseURL', async () => {
    expect(axios.create).toHaveBeenCalledWith(
      expect.objectContaining({
        baseURL: '/api/v1',
        headers: { 'Content-Type': 'application/json' },
        withCredentials: true,
      })
    );
  });

  it('registers request and response interceptors', async () => {
    const instance = (axios.create as any).mock.results[0].value;
    expect(instance.interceptors.request.use).toHaveBeenCalled();
    expect(instance.interceptors.response.use).toHaveBeenCalled();
  });

  describe('request interceptor', () => {
    it('does not attach Authorization header for cookie-based auth', () => {
      const config = { headers: {} as any, method: 'get', url: '/hosts' };
      const result = requestFulfilled(config);
      expect(result.headers.Authorization).toBeUndefined();
    });
  });

  describe('response interceptor - 401 handling', () => {
    it('attempts cookie-based refresh on 401 for non-auth endpoints', async () => {
      (axios.post as any).mockResolvedValueOnce({ data: { ok: true } });
      const error = { response: { status: 401 }, config: {} };

      try {
        await responseRejected(error);
      } catch {
        // api instance retry is not fully mocked here
      }

      expect(axios.post).toHaveBeenCalledWith(
        '/api/v1/auth/refresh',
        undefined,
        expect.objectContaining({ withCredentials: true }),
      );
    });

    it('does not attempt refresh for auth login failures', async () => {
      const error = {
        response: { status: 401 },
        config: { headers: {} as any, url: '/auth/login' },
      };

      try {
        await responseRejected(error);
      } catch {
        // expected rejection
      }

      expect(axios.post).not.toHaveBeenCalled();
    });

    it('does not retry if __retry flag is already set', async () => {
      const error = {
        response: { status: 401 },
        config: { headers: {}, __retry: true },
      };

      Object.defineProperty(window, 'location', {
        writable: true,
        value: { pathname: '/login', href: '/login' },
      });

      try {
        await responseRejected(error);
      } catch {
        // expected
      }

      expect(axios.post).not.toHaveBeenCalled();
    });

    it('does not invoke auth-failure handler when already on /login after terminal 401', async () => {
      const error = {
        response: { status: 401 },
        config: { headers: {}, __retry: true, url: '/auth/me' },
      };

      Object.defineProperty(window, 'location', {
        writable: true,
        value: { pathname: '/login', href: '/login' },
      });

      try {
        await responseRejected(error);
      } catch {
        // expected rejection
      }

      expect(mocks.authFailureHandler).not.toHaveBeenCalled();
    });

    it('invokes auth-failure handler after terminal 401 on a protected route', async () => {
      // refresh must fail so the interceptor falls through to the handler
      (axios.post as any).mockRejectedValueOnce(new Error('401'));
      const error = {
        response: { status: 401 },
        config: { headers: {}, __retry: true, url: '/hosts' },
      };

      Object.defineProperty(window, 'location', {
        writable: true,
        value: { pathname: '/dashboard', href: '/dashboard' },
      });

      try {
        await responseRejected(error);
      } catch {
        // expected rejection
      }

      expect(mocks.authFailureHandler).toHaveBeenCalledTimes(1);
    });
  });

  describe('api namespace methods', () => {
    it('exports hosts, devices, plans, planRuns, and other namespaces', async () => {
      const { api } = await import('./api');
      expect(api.hosts).toBeDefined();
      expect(api.hosts.list).toBeInstanceOf(Function);
      expect(api.hosts.create).toBeInstanceOf(Function);
      expect(api.hosts.update).toBeInstanceOf(Function);
      expect(api.devices).toBeDefined();
      expect(api.devices.list).toBeInstanceOf(Function);
      expect(api.plans).toBeDefined();
      expect(api.plans.list).toBeInstanceOf(Function);
      expect(api.plans.delete).toBeInstanceOf(Function);
      expect(api.planRuns).toBeDefined();
      expect(api.planRuns.list).toBeInstanceOf(Function);
      expect(api.auth).toBeDefined();
      expect(api.auth.login).toBeInstanceOf(Function);
      expect(api.results).toBeDefined();
      expect(api.stats).toBeDefined();
      expect(api.notifications).toBeDefined();
      expect(api.schedules).toBeDefined();
      expect(api.audit).toBeDefined();
      expect(api.scripts).toBeDefined();
      expect(api.scripts.list).toBeInstanceOf(Function);
      expect(api.actionTemplates).toBeDefined();
      expect(api.actionTemplates.list).toBeInstanceOf(Function);
    });
  });
});
