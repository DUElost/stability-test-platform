# Surface Product and Version filters directly in DevicesPage primary toolbar

Status: implemented
Class: feature

## Decision

Promote the frequently used `Product` (`device-product-filter`) and `版本` (`device-version-filter`) `SearchableSelect` dropdowns out of `MoreFiltersPopover` directly onto the main `ExpandableDeviceTable` filter toolbar alongside Project, Host, and Model filters:

- Keep `MoreFiltersPopover` exclusively for low-frequency scope toggles (`显示陈旧设备`, `显示已退役设备`), rendering it only when at least one toggle handler is provided, and scope `moreActiveCount` / `hiddenChips` to those two toggles so active filter chips are not duplicated with visible toolbar controls.
- Tune dropdown widths (`sm:w-[120px] xl:w-32`, Product `sm:w-[132px] xl:w-36`, `xl:flex-nowrap`) so all 6 controls remain on a single horizontal line at both `1280×800` and `1440×900` without horizontal overflow or placeholder crowding.

## Alternatives

- Keeping Product and Version folded inside `MoreFiltersPopover`: rejected based on real operator workflow feedback — both filters are used routinely on `/devices` and require single-click access.

## Verification

- `npx vitest run src/components/device/ExpandableDeviceTable.test.tsx src/pages/devices/DevicesPage.test.tsx`
- `pytest tests/test_frontend_filter_workbench_guard.py -v`
- `npx tsc --noEmit`, `npx eslint --max-warnings 0`
- Isolated `stp-ui-review` real-browser check at `1440×900` & `1280×800` confirming single-line toolbar layout (`filterWrapLines: 1`, `docOverflowX: 0`).

## Revisit

- If additional high-frequency filter dimensions are added to `/devices` in Phase 2, revisit responsive collapse priority below `1280px`.
