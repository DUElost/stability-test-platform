# Host group WiFi pool allocation (#956)

Status: implemented

## Decision

Enforce `ResourcePool.host_group` during `_sync_allocate_devices`: pools with a
non-empty `host_group` are only eligible when the device's host id, name, or
hostname matches that value. Global pools (`host_group` null/blank) remain
available to every host. Dispatch passes `device_host_map` from the existing
device classification step.

## Alternatives

- **Doc-only**: mark the field as decorative — rejected; UI promises host
  restriction.
- **Separate Host.host_group column**: deferred; operators already store host
  id/name in the pool field per WiFi page placeholder.

## Verification

- `pytest backend/tests/services/test_wifi_optional_at_run_time.py -k host_group`
- Existing WiFi allocation tests unchanged (pools without `host_group`).

## Revisit

If hosts gain a formal group dimension distinct from id/name, extend
`_host_match_keys` without changing pool schema.
