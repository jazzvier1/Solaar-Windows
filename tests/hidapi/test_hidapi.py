import platform

from unittest import mock

import pytest

if platform.system() == "Linux":
    import hidapi.udev_impl as hidapi
else:
    import hidapi.hidapi_impl as hidapi


def test_find_paired_node():
    hidapi.enumerate(mock.Mock())


@pytest.mark.skipif(platform.system() == "Linux", reason="hidapi backend only")
def test_match_windows_uses_collection_usage():
    base = dict(
        vendor_id=0x046D,
        product_id=0x0AB5,
        bus_type=0x01,
        path=b"p",
        manufacturer_string="",
        product_string="G733",
        serial_number="",
        release_number=0,
    )
    with mock.patch("platform.system", return_value="Windows"):
        hidpp = hidapi._match("add", dict(base, usage_page=0xFF43, usage=0x0202), lambda *a: {"isDevice": True})
        audio = hidapi._match("add", dict(base, usage_page=0xFF00, usage=0x0001), lambda *a: {"isDevice": True})
        mouse = hidapi._match("add", dict(base, usage_page=0xFF00, usage=0x0002), lambda *a: {"isDevice": True})
        receiver = hidapi._match("add", dict(base, usage_page=0xFF00, usage=0x0002), lambda *a: {"isDevice": False})
        razer = hidapi._match(
            "add", dict(base, vendor_id=0x1532, usage_page=0xFF00, usage=0x0002), lambda *a: {"isDevice": True}
        )
    assert hidpp.hidpp_long and not hidpp.hidpp_short
    assert audio is None
    assert mouse.hidpp_long
    assert receiver is None
    assert razer is None


RECEIVER = dict(
    vendor_id=0x046D,
    product_id=0xC52B,
    bus_type=0x01,
    manufacturer_string="Logitech",
    product_string="USB Receiver",
    serial_number="",
    release_number=0x1200,
)
RECEIVER_PREFIX = b"\\\\?\\hid#vid_046d&pid_c52b&mi_02&col0"
RECEIVER_SUFFIX = b"#8&1a2b3c4d&0&000%d#{4d1e55b2-f16f-11cf-88cb-001111000030}"  # the instance ID ends in the collection


def _collection(number, usage):
    path = RECEIVER_PREFIX + b"%d" % number + RECEIVER_SUFFIX % number
    return dict(RECEIVER, path=path, usage_page=0xFF00, usage=usage)


@pytest.mark.skipif(platform.system() == "Linux", reason="hidapi backend only")
def test_match_windows_reports_a_receiver_once_through_its_short_collection():
    is_receiver = lambda *a: {"isDevice": False}  # noqa: E731
    with mock.patch("platform.system", return_value="Windows"):
        short, long, dj = (hidapi._match("add", _collection(n, u), is_receiver) for n, u in ((1, 1), (2, 2), (3, 4)))
    assert short.hidpp_short and short.hidpp_long and not short.isDevice
    assert short.path.encode().endswith(RECEIVER_SUFFIX % 1)
    assert long is None
    assert dj is None


@pytest.mark.skipif(platform.system() == "Linux", reason="hidapi backend only")
def test_receiver_collections_are_found_from_the_short_collection():
    collections = [_collection(1, 1), _collection(2, 2), _collection(3, 4)]
    other_interface = dict(_collection(1, 6), path=RECEIVER_PREFIX.replace(b"mi_02", b"mi_00") + b"1#other")
    with mock.patch("platform.system", return_value="Windows"), mock.patch.object(
        hidapi, "_enumerate_collections", return_value=collections + [other_interface]
    ):
        found = hidapi._receiver_collection_paths(collections[0]["path"])
        from_long = hidapi._receiver_collection_paths(collections[1]["path"])
    assert found == {0x10: collections[0]["path"], 0x11: collections[1]["path"], 0x20: collections[2]["path"]}
    assert from_long is None


@pytest.mark.skipif(platform.system() == "Linux", reason="hidapi backend only")
def test_receiver_handle_routes_writes_and_merges_reads():
    reports = {1: [b"\x10\x01\x8f\x00\x00\x00\x00"], 2: [b"\x11\x01" + bytes(18)], 3: []}
    written = []

    def fake_read(handle, size, timeout_ms=None):
        if reports[handle]:
            return reports[handle].pop(0)
        hidapi.sleep(0.01)
        return b""

    def fake_write(handle, data):
        written.append((handle, data))
        return len(data)

    with mock.patch.object(hidapi, "_raw_read", fake_read), mock.patch.object(hidapi, "_raw_write", fake_write):
        with mock.patch.object(hidapi._hidapi, "hid_close", mock.Mock()) as hid_close:
            receiver = hidapi._ReceiverHandle({0x10: 1, 0x11: 2, 0x20: 3})
            hidapi._receiver_handles[99] = receiver
            seen = {hidapi.read(99, 64, 1000), hidapi.read(99, 64, 1000)}
            assert hidapi.read(99, 64, 20) == b""
            hidapi.write(99, b"\x10\x01\x81\x00\x00\x00\x00")
            hidapi.write(99, b"\x11\x01" + bytes(18))
            hidapi.write(99, b"\x20\x01" + bytes(13))
            hidapi.close(99)
    assert seen == {b"\x10\x01\x8f\x00\x00\x00\x00", b"\x11\x01" + bytes(18)}
    assert [handle for handle, _ in written] == [1, 2, 3]
    assert hid_close.call_count == 3
    assert 99 not in hidapi._receiver_handles


@pytest.mark.skipif(platform.system() == "Linux", reason="hidapi backend only")
def test_receiver_handle_read_fails_once_a_collection_fails():
    def fake_read(handle, size, timeout_ms=None):
        raise hidapi.HIDError("unplugged")

    with mock.patch.object(hidapi, "_raw_read", fake_read), mock.patch.object(hidapi._hidapi, "hid_close", mock.Mock()):
        receiver = hidapi._ReceiverHandle({0x10: 1, 0x11: 2})
        hidapi._receiver_handles[98] = receiver
        for _ in range(2):
            with pytest.raises(hidapi.HIDError):
                hidapi.read(98, 64, 1000)
        hidapi.close(98)
