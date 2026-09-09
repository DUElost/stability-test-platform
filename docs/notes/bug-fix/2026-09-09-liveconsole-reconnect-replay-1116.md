# LiveConsole 重连后补齐日志缺口（#1116）

Status: implemented  
Class: bug-fix

## Decision

`LiveConsole` 增加 `fillGap`：按 `seqRef+1` 增量 `getRunLog`。断线重连
（曾 connected 后再 connected）触发补齐；live 批次 `from_seq` 跳号时同样
触发。`applyLines` 按行跳过与已写区间重叠，避免 replay/live 重复。

涉及：`frontend/src/components/console/LiveConsole.tsx`；测试见
`LiveConsole.test.tsx`。

## Alternatives

- 仅全量 replayFromStart：能补齐但清屏闪烁，且丢失滚动位置。
- 只在 reconnect 补、不检 live gap：仍可能在半连接状态留下空洞。

## Verification

- `vitest --run src/components/console/LiveConsole.test.tsx`
- 覆盖断线完成交错 + live 重叠跳过

## Revisit

若高吞吐下 gap fill 与 live 并发 thrash，可为 fill 加短 debounce。
