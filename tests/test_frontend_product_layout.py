"""Long ADB product values must not widen device rows or their select controls.

jsdom cannot measure overflow. Pin bounded, discoverable text on the two table
surfaces; semantic filtering/selection remains covered by the component tests.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("surface", [
    "components/device/ExpandableDeviceTable.tsx",
    "components/execution/plan-execute/DeviceTablePanel.tsx",
])
def test_product_text_is_bounded_and_full_value_is_available(surface):
    source = (ROOT / "frontend/src" / surface).read_text()
    product = re.search(
        r'<div className="([^"]+)" title=\{deviceProductText\(device.model, device.product\)\}>\s*'
        r'Product: \{deviceProductText\(device.model, device.product\)\}',
        source,
    )
    assert product, "Product must remain visible with its full value in title"
    classes = product.group(1).split()
    assert "truncate" in classes
    assert any(value.startswith("max-w-") for value in classes)
