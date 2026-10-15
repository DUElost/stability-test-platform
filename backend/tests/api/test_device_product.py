"""Preserve measured product independently of model and online status."""

import pytest


def report(client, host, devices):
    response = client.post("/api/v1/heartbeat", json={
        "host_id": host.id, "status": "ONLINE", "devices": devices,
    })
    assert response.status_code == 200


def test_same_model_products_round_trip_and_filter(client, sample_host, auth_headers):
    report(client, sample_host, [
        {"serial": "PRODUCT-NX3", "model": "MLD_NX3", "product": "MLD-NX3",
         "adb_connected": True, "adb_state": "device"},
        {"serial": "PRODUCT-CARRIER", "model": "MLD_NX3", "product": "sys_mssi_64_cn_armv82",
         "adb_connected": False, "adb_state": "offline"},
    ])
    response = client.get("/api/v1/devices?product=sys_mssi_64_cn_armv82&skip=0&limit=1", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["serial"] == "PRODUCT-CARRIER"
    assert item["model"] == "MLD_NX3"
    assert item["product"] == "sys_mssi_64_cn_armv82"
    assert item["status"] == "OFFLINE"
    assert client.get(f"/api/v1/devices/{item['id']}", headers=auth_headers).json()["product"] == item["product"]


@pytest.mark.parametrize("value", [None, "", "  ", 123, "x" * 129])
def test_missing_or_invalid_product_does_not_erase_known_value(client, sample_host, auth_headers, value):
    device = {"serial": "PRODUCT-LEGACY", "model": "MLD_NX3", "product": "MLD-NX3",
              "adb_connected": True, "adb_state": "device"}
    report(client, sample_host, [device])
    device["product"] = value
    report(client, sample_host, [device])
    device.pop("product")
    report(client, sample_host, [device])
    items = client.get("/api/v1/devices?product=MLD-NX3", headers=auth_headers).json()
    assert any(d["serial"] == "PRODUCT-LEGACY" for d in items)


def test_new_measurement_replaces_product_and_legacy_device_is_unknown(client, sample_host, auth_headers):
    device = {"serial": "PRODUCT-CHANGE", "model": "MLD_NX3", "product": "MLD-NX3"}
    report(client, sample_host, [device, {"serial": "PRODUCT-UNKNOWN", "model": "MLD_NX3"}])
    device["product"] = "sys_mssi_64_cn_armv82"
    report(client, sample_host, [device])
    items = client.get("/api/v1/devices?include_stale=true", headers=auth_headers).json()
    by_serial = {d["serial"]: d for d in items}
    assert by_serial["PRODUCT-CHANGE"]["product"] == "sys_mssi_64_cn_armv82"
    assert by_serial["PRODUCT-UNKNOWN"]["product"] is None


def test_product_filter_validation_and_auth(client, auth_headers):
    assert client.get("/api/v1/devices?product=MLD-NX3").status_code == 401
    assert client.get("/api/v1/devices?product=", headers=auth_headers).status_code == 422
    assert client.get("/api/v1/devices?product=" + "x" * 129, headers=auth_headers).status_code == 422
