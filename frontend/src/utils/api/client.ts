import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    if (import.meta.env.DEV) console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    return config;
  },
  (error) => {
    if (import.meta.env.DEV) console.error('[API] Request error:', error);
    return Promise.reject(error);
  }
);

apiClient.interceptors.response.use(
  (response) => {
    if (import.meta.env.DEV) console.log(`[API] Response:`, response.data);
    return response;
  },
  async (error) => {
    if (import.meta.env.DEV) console.error('[API] Response error:', error);

    if (error.response?.status === 401) {
      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken && error.config && !error.config.__retry) {
        error.config.__retry = true;
        try {
          const response = await axios.post('/api/v1/auth/refresh', {
            refresh_token: refreshToken,
          });
          const { access_token, refresh_token } = response.data;
          localStorage.setItem('access_token', access_token);
          localStorage.setItem('refresh_token', refresh_token);

          error.config.headers.Authorization = `Bearer ${access_token}`;
          return apiClient(error.config);
        } catch (refreshError) {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          window.location.href = '/login';
          return Promise.reject(refreshError);
        }
      } else {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        window.location.href = '/login';
      }
    }

    return Promise.reject(error);
  }
);

export default apiClient;

export async function unwrapApiResponse<T>(request: Promise<{ data: { data?: T; error?: { code: string; message: string } | null } }>): Promise<T> {
  const resp = await request;
  const body = resp.data as any;
  if (body?.error) throw new Error(`[${body.error.code}] ${body.error.message}`);
  return body?.data ?? body;
}
