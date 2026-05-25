/**
 * Query key factories for consistent react-query cache management.
 *
 * Each factory produces query keys with the same structure that react-query
 * uses for deep equality matching.  Components that subscribe to the same
 * data with different query parameters use distinct keys to prevent
 * cross-consumer cache collisions.
 */

export const planKeys = {
  /** Plan list queries — scoped by limit to avoid cache collision between
   *  PlanListPage (limit=100) and PlanExecutePage (limit=100).
   */
  list: (limit: number) => ['plans', { limit }] as const,

  /** Invalidation key that matches ALL plan list queries regardless of limit.
   *  react-query partial matching: ['plans'] matches ['plans', {limit: X}].
   */
  allLists: () => ['plans'] as const,
} as const;
