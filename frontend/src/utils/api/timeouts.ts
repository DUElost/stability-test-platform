/**
 * API 应用层超时策略（#1199）。
 *
 * 默认给所有 apiClient 请求上界：普通接口挂起时不再无限 pending，用户可重试。
 * refresh / 会话探活是 401 恢复的共享路径（in-flight Promise + 跨标签 Web
 * Lock），超时收紧——挂起会阻塞全站恢复队列（后续 401 排队等待）。
 *
 * 长请求（LLM 推理、文件上传/下载）显式豁免为 NO_TIMEOUT：由浏览器/代理兜底，
 * 避免应用层把正常的长耗时误判为失败。
 */

/** apiClient 默认超时（普通 REST）。 */
export const API_TIMEOUT_MS = 30_000;

/** refresh 请求超时（401 恢复共享路径，收紧）。 */
export const AUTH_REFRESH_TIMEOUT_MS = 10_000;

/** 会话探活（/auth/me）超时。 */
export const SESSION_PROBE_TIMEOUT_MS = 8_000;

/** 长请求豁免：0 = 不设应用层超时。 */
export const NO_TIMEOUT = 0;
