import type { PaginatedResponse } from './types';

/** 全量结果：只带 `items` 与 `total`——`skip`/`limit` 属于"某一页"，在这里没有意义。 */
export type PagedResult<T> = Pick<PaginatedResponse<T>, 'items' | 'total'>;

/** 取一页：`skip` 为已取条数，`limit` 为页大小。 */
export type PageFetcher<T> = (skip: number, limit: number) => Promise<PaginatedResponse<T>>;

/**
 * 按服务端 `total` 翻页拉全量，并**连同 `total` 一并返回**——`total` 是判「有没有拿全」
 * 的唯一依据，`items.length` 不是总数。
 *
 * 为什么必须翻页：端点的 `limit` 是**单次响应护栏**（体积护栏），不等于集合总量。
 * 任何"请求一次拿全部"的写法都会在集合增长后静默少数据——设备页（#3131）、项目详情页
 * （#3134）、计划列表（#3147）都是同一个病。要全量就得翻页，并把
 * `items.length < total` 显式提示出来。
 *
 * 两个出口都要有：拿齐了（`items.length >= total`）正常退出；服务端 `total` 与实际
 * 不符时（并发增删）靠空页退出——否则死循环。
 *
 * **前提**：服务端排序必须是**跨请求不变的全序**，否则 offset 分页会在两次请求之间
 * 重复/漏行（#3123 实测：旧排序下页大小 200 + 页间 150ms，5 轮中 3 轮各丢 14-20 台）。
 * 消费方无法自证这一点，只能依赖端点侧维持该性质。
 *
 * `pageLimit` 由调用方按端点的 `le` 上限给（取值只需 ≤ `le`，只影响请求次数）。
 */
export async function fetchAllPages<T>(
  fetchPage: PageFetcher<T>,
  pageLimit: number,
): Promise<PagedResult<T>> {
  let page = await fetchPage(0, pageLimit);
  const items = [...page.items];
  while (page.items.length > 0 && items.length < page.total) {
    page = await fetchPage(items.length, pageLimit);
    items.push(...page.items);
  }
  return { items, total: page.total };
}
