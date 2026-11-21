"""Find disks and what they claim to be, via PowerShell (no admin needed)."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

# For each disk: Storage-module info, its partitions/volumes, and the USB device it hangs off.
# The disk's PnP ID (USBSTOR\... or SCSI\... for UAS) has no VID/PID, so walk up the
# device tree (DEVPKEY_Device_Parent) until we hit USB\VID_xxxx&PID_xxxx.
_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$pnp = @{}
Get-CimInstance Win32_DiskDrive | ForEach-Object { $pnp[[int]$_.Index] = $_.PNPDeviceID }
$out = foreach ($d in Get-Disk) {
  $id = $pnp[[int]$d.Number]; $usb = $null; $cur = $id
  for ($i = 0; $i -lt 6 -and $cur; $i++) {
    if ($cur -match '^USB\\VID_') { $usb = $cur; break }
    $cur = (Get-PnpDeviceProperty -InstanceId $cur -KeyName DEVPKEY_Device_Parent).Data
  }
  $parts = foreach ($p in (Get-Partition -DiskNumber $d.Number)) {
    $v = $p | Get-Volume
    [pscustomobject]@{ Letter = [string]$p.DriveLetter; Size = [int64]$p.Size
      Free = [int64]$v.SizeRemaining; FileSystem = [string]$v.FileSystem; Label = [string]$v.FileSystemLabel }
  }
  [pscustomobject]@{ Number = [int]$d.Number; Model = [string]$d.FriendlyName; Bus = [string]$d.BusType
    Size = [int64]$d.Size; Serial = [string]$d.SerialNumber; IsSystem = [bool]$d.IsSystem
    IsBoot = [bool]$d.IsBoot; PartitionStyle = [string]$d.PartitionStyle; PnpId = [string]$id
    UsbId = [string]$usb; Partitions = @($parts) }
}
ConvertTo-Json -InputObject @($out) -Depth 4 -Compress
"""


@dataclass
class Volume:
    letter: str          # "E" or "" if none
    size: int
    free: int
    filesystem: str
    label: str


@dataclass
class Disk:
    number: int
    model: str
    bus: str
    size: int
    serial: str
    is_system: bool
    is_boot: bool
    partition_style: str
    pnp_id: str
    usb_id: str
    vid: str | None = None
    pid: str | None = None
    volumes: list[Volume] = field(default_factory=list)

    @property
    def letters(self) -> list[str]:
        return [v.letter for v in self.volumes if v.letter]

    @property
    def partitioned(self) -> int:
        return sum(v.size for v in self.volumes)


def _vid_pid(usb_id: str) -> tuple[str | None, str | None]:
    m = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", usb_id or "", re.I)
    return (m.group(1).upper(), m.group(2).upper()) if m else (None, None)


def parse_disks(json_text: str) -> list[Disk]:
    raw = json.loads(json_text or "[]")
    if isinstance(raw, dict):
        raw = [raw]
    disks = []
    for d in raw:
        vols = []
        for p in d.get("Partitions") or []:
            letter = (p.get("Letter") or "").strip("\x00 ").upper()
            vols.append(Volume(letter if letter.isalpha() else "", int(p.get("Size") or 0),
                               int(p.get("Free") or 0), p.get("FileSystem") or "", p.get("Label") or ""))
        vid, pid = _vid_pid(d.get("UsbId") or "")
        disks.append(Disk(
            number=int(d["Number"]), model=(d.get("Model") or "").strip(), bus=d.get("Bus") or "",
            size=int(d.get("Size") or 0), serial=(d.get("Serial") or "").strip(),
            is_system=bool(d.get("IsSystem")), is_boot=bool(d.get("IsBoot")),
            partition_style=d.get("PartitionStyle") or "", pnp_id=d.get("PnpId") or "",
            usb_id=d.get("UsbId") or "", vid=vid, pid=pid, volumes=vols))
    return disks


def list_disks() -> list[Disk]:
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0 and not out.stdout.strip():
        raise RuntimeError(f"PowerShell disk query failed: {out.stderr.strip()[:500]}")
    return parse_disks(out.stdout.strip())


def disk_for_letter(disks: list[Disk], letter: str) -> tuple[Disk, Volume] | None:
    letter = letter.strip().rstrip(":\\/").upper()
    for d in disks:
        for v in d.volumes:
            if v.letter == letter:
                return d, v
    return None
