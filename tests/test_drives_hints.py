"""Drive parsing and red flags, using the shape of real counterfeit drives as fixtures."""
import json

from fakecheck import drives, hints

# Modeled on a counterfeit "20 TB SSD": placeholder ITE ID, no serial, round size, rebuilt to 50 GiB.
FAKE_SSD = {
    "Number": 1, "Model": "FS SSD", "Bus": "USB", "Size": 20_971_520_000_000, "Serial": "",
    "IsSystem": False, "IsBoot": False, "PartitionStyle": "MBR",
    "PnpId": "USBSTOR\\DISK&VEN_FS&PROD_SSD\\7&123&0", "UsbId": "USB\\VID_048D&PID_1234\\0001",
    "Partitions": [{"Letter": "D", "Size": 53_687_091_200, "Free": 50_000_000_000,
                    "FileSystem": "exFAT", "Label": "FS-53GB"}],
}
SYSTEM_NVME = {
    "Number": 0, "Model": "Example NVMe SSD", "Bus": "NVMe", "Size": 512_110_190_592,
    "Serial": "0000_0000_TEST", "IsSystem": True, "IsBoot": True, "PartitionStyle": "GPT",
    "PnpId": "SCSI\\DISK&VEN_NVME", "UsbId": "",
    "Partitions": [{"Letter": "\u0000", "Size": 104857600, "Free": 0, "FileSystem": "", "Label": ""},
                   {"Letter": "C", "Size": 171804170240, "Free": 35 * 2**30, "FileSystem": "NTFS",
                    "Label": ""}],
}
GENUINE_STICK = {
    "Number": 2, "Model": "SanDisk Ultra", "Bus": "USB", "Size": 61_530_439_680, "Serial": "4C530001",
    "IsSystem": False, "IsBoot": False, "PartitionStyle": "MBR",
    "PnpId": "USBSTOR\\X", "UsbId": "USB\\VID_0781&PID_5581\\4C530001",
    "Partitions": [{"Letter": "F", "Size": 61_529_391_104, "Free": 61_000_000_000,
                    "FileSystem": "exFAT", "Label": ""}],
}


def _disks(*raw):
    return drives.parse_disks(json.dumps(list(raw)))


def test_parse_extracts_ids_and_skips_letterless_partitions():
    fake, nvme = _disks(FAKE_SSD, SYSTEM_NVME)
    assert (fake.vid, fake.pid) == ("048D", "1234")
    assert fake.letters == ["D"]
    assert nvme.letters == ["C"]
    assert nvme.vid is None


def test_parse_single_object_json():
    assert len(drives.parse_disks(json.dumps(FAKE_SSD))) == 1


def test_fake_ssd_lights_up():
    (fake,) = _disks(FAKE_SSD)
    hs = hints.hints(fake)
    levels = [h.level for h in hs]
    text = " ".join(h.text for h in hs)
    assert "red" in levels
    assert "No serial" in text
    assert "round" in text
    assert "TB over USB" in text
    assert "partitioned" in text


def test_genuine_stick_is_quiet():
    (stick,) = _disks(GENUINE_STICK)
    assert all(h.level == "info" for h in hints.hints(stick))


def test_system_and_internal_disks_are_blocked():
    fake, nvme = _disks(FAKE_SSD, SYSTEM_NVME)
    assert hints.blockers(fake, fake.volumes[0]) == []
    why = hints.blockers(nvme, nvme.volumes[1])
    assert any("system" in w for w in why)
    assert any("internal" in w for w in why)
    # even with --allow-non-usb, the system disk stays blocked
    assert hints.blockers(nvme, nvme.volumes[1], allow_non_usb=True)


def test_disk_for_letter():
    disks = _disks(FAKE_SSD, SYSTEM_NVME)
    d, v = drives.disk_for_letter(disks, "d:\\")
    assert d.model == "FS SSD" and v.letter == "D"
    assert drives.disk_for_letter(disks, "Z") is None
