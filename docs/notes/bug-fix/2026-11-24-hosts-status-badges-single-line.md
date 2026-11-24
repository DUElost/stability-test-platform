# Keep HostsPage status and fault chips on a single non-wrapping line

Status: implemented
Class: bug-fix

## Decision

Keep all status badges and inline fault chips (`在线`, `降级`/`禁调`, `USB 空树`, etc.) in `ExpandableHostTable` on a single `flex-nowrap whitespace-nowrap` horizontal row above the `Watch` status line, so hosts with 3+ status/fault badges retain the exact same 2-line row height as healthy hosts.

## Alternatives

- Allowing `flex-wrap` and rendering inline fault chips in a separate block row below `StatusBadge`: rejected because any degraded host with a fault chip (`在线` + `降级` + `USB 空树`) becomes 3 lines tall, causing uneven row heights across the virtualized table.
- Hiding fault chips from the main row completely: rejected because primary USB/Hub fault chips (`USB 空树`, `USB 链路劣化`, etc.) provide high-signal triage without opening the drawer.

## Verification

- `npx vitest run src/components/network/ExpandableHostTable.test.tsx src/pages/hosts/HostsPage.test.tsx`
- `npx tsc --noEmit`, `npx eslint --max-warnings 0`, and `scripts/run_gates.py check:quick`
- Isolated `stp-ui-review` real-browser verification confirming uniform row height when a host carries 3+ status badges.

## Revisit

- If a host routinely accumulates >4 long status badges simultaneously on narrow viewports (`1280×800`), revisit capping inline fault chips to 1 + `+N` overflow badge while preserving the full list in `HostDetailDrawer`.
