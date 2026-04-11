#!/usr/bin/env python3

from dataclasses import dataclass
from enum import Enum
import os
from typing import Any, Dict, Optional, Tuple

from .cmd import BaseCmd
from .utils import run, add_mp_to_fstab, CLOUDRIFT_MEDIA_MOUNT, yes_no_prompt
import json
import shutil
import subprocess


MIN_DISK_SPACE_GB = 1000.0

class DiskType(str, Enum):
    SSD = "ssd"
    HDD = "hdd"
    NVME = "nvme"


@dataclass(frozen=True)
class DiskInfo:
    path: str
    type: str
    mountpoint: Optional[str]
    rota: Optional[str]
    tran: str
    size_bytes: Optional[int] = None

    @property
    def size_gb(self) -> Optional[float]:
        if self.size_bytes is None:
            return None
        return self.size_bytes / (1024 ** 3)


def get_lvm_free_space(min_disk_space_gb: float = MIN_DISK_SPACE_GB) -> Optional[Tuple[str, float]]:
    """
    Check for free space in LVM volume groups.
    Returns (vg_name, free_gb) or None if no free space available.
    """
    try:
        # Get volume group info in JSON format
        out, _, rc = run(["vgs", "--reportformat", "json", "--units", "g"], capture_output=True, quiet_stderr=True)
        if rc != 0:
            return None

        data = json.loads(out)
        for vg in data.get("report", [{}])[0].get("vg", []):
            vg_name = vg.get("vg_name", "")
            vg_free = vg.get("vg_free", "0g")

            # Parse free space value (remove 'g' suffix and convert to float)
            free_gb = float(vg_free.rstrip('g'))

            if free_gb >= min_disk_space_gb:
                print(f"Found {free_gb:.1f}GB free space in volume group '{vg_name}' (minimum required: {min_disk_space_gb:.1f}GB)")
                return vg_name, free_gb
            else:
                print(f"Volume group '{vg_name}' has {free_gb:.1f}GB free — below minimum {min_disk_space_gb:.1f}GB, skipping.")

    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
        print(f"Could not check LVM free space: {e}")

    return None

def create_lvm_logical_volume(vg_name: str) -> str:
    """
    Create a logical volume using all free space in the volume group.
    Returns the device path of the created logical volume.
    """
    lv_name = "cloudrift"

    # Create logical volume using 100% of free space
    print(f"Creating logical volume '{lv_name}' in volume group '{vg_name}'")
    run(["lvcreate", "-l", "100%FREE", "-n", lv_name, vg_name])

    # Return the device path
    lv_path = f"/dev/{vg_name}/{lv_name}"
    print(f"Created logical volume: {lv_path}")
    return lv_path

def find_unused_whole_disks() -> list[DiskInfo]:
    # Use lsblk JSON; suppress stderr warnings like "not a block device"
    out, _, _ = run(
        ["lsblk", "-J", "-b", "-o", "NAME,TYPE,MOUNTPOINT,ROTA,TRAN,SIZE"],
        capture_output=True,
        quiet_stderr=True,
    )
    data = json.loads(out)
    disks: list[DiskInfo] = []
    for dev in data.get("blockdevices", []):
        # Select only whole disks: type=="disk", no children, no mountpoint
        if (
            dev.get("type") == "disk"
            and not dev.get("children")
            and dev.get("mountpoint") in (None, "")
        ):
            name = dev.get("name")
            if not name:
                continue
            raw_size = dev.get("size")
            size_bytes = int(raw_size) if raw_size is not None else None
            disks.append(
                DiskInfo(
                    path=f"/dev/{name}",
                    type=str(dev.get("type", "")),
                    mountpoint=dev.get("mountpoint"),
                    rota=str(dev.get("rota")) if dev.get("rota") is not None else None,
                    tran=str(dev.get("tran", "")).lower(),
                    size_bytes=size_bytes,
                )
            )
    return disks

def reload_daemon():
    run(["systemctl", "daemon-reload"])

def add_to_fstab(dev, mp):
    run(["udevadm", "trigger"])
    uuid, _, _ = run(["blkid", "-s", "UUID", "-o", "value", dev], capture_output=True)
    print(f"Adding {dev} with UUID {uuid} to /etc/fstab at mount point {mp}")
    # For LVM volumes, use noatime and defaults, for regular disks use nofail and discard
    if "/dev/mapper/" in dev or "-vg-" in dev:
        fstab_line = f"UUID={uuid} {mp} ext4 defaults,noatime 0 2\n"
    else:
        fstab_line = f"UUID={uuid} {mp} ext4 defaults,nofail,discard 0 0\n"
    add_mp_to_fstab(fstab_line, mp)


def mount_media_disk(dev, mp):
    run(["mkdir", "-p", mp])
    run(["mount", dev, mp])

def create_filesystem(dev, label="cloudrift"):
    # Use -m 0 to reserve 0% for root (maximizing available space)
    run(["mkfs.ext4", "-m", "0", "-L", label, dev])

def check_disk_type(disks: list[DiskInfo], disk_type: DiskType) -> bool:
    if not disks:
        return False

    if not isinstance(disk_type, DiskType):
        return False

    for disk in disks:
        if disk.type != "disk":
            return False

        rota = (disk.rota or "").strip()
        transport = disk.tran.strip().lower()
        is_nvme = transport == "nvme" or os.path.basename(disk.path).startswith("nvme")
        is_hdd = rota == "1"
        is_ssd = rota == "0"

        if disk_type == DiskType.NVME and not is_nvme:
            return False
        if disk_type == DiskType.HDD and not is_hdd:
            return False
        if disk_type == DiskType.SSD and not is_ssd:
            return False

    return True

def create_raid_array(disks):
    cmd = ["mdadm", "--create", "--verbose", "/dev/md0", "--level=0", "--raid-devices={}".format(len(disks))]
    devices = [disk.path for disk in disks]
    print("Creating RAID 0 array with devices: {}".format(devices))
    cmd.extend(devices)
    run(cmd)

def configure_lvm_storage(vg_name: str, free_gb: float) -> None:
    """
    Configure storage using LVM free space.

    Args:
        vg_name: Volume group name with free space
        free_gb: Amount of free space in GB
    """
    print(f"Using LVM free space: {free_gb:.1f}GB in volume group '{vg_name}'")

    if yes_no_prompt("Do you want to proceed?", default=True) is False:
        print("Operation cancelled by user.")    
        return None  # Not an error, just cancelled

    # Create logical volume
    lv_path = create_lvm_logical_volume(vg_name)

    # Create filesystem
    create_filesystem(lv_path)

    # Mount the logical volume
    mount_media_disk(lv_path, CLOUDRIFT_MEDIA_MOUNT)

    # Add to fstab (will use the device mapper path)
    # The actual device path might be /dev/mapper/vg_name-lv_name
    mapper_path = f"/dev/mapper/{vg_name.replace('-', '--')}-cloudrift"
    if os.path.exists(mapper_path):
        add_to_fstab(mapper_path, CLOUDRIFT_MEDIA_MOUNT)
    else:
        add_to_fstab(lv_path, CLOUDRIFT_MEDIA_MOUNT)

    reload_daemon()
    print(f"Successfully configured LVM logical volume at {CLOUDRIFT_MEDIA_MOUNT}")


def configure_regular_disks(disks: list[DiskInfo], min_disk_space_gb: float = 10.0) -> None:
    """
    Configure storage using regular disks (single disk or RAID).

    Args:
        disks: List of unused disk metadata
        min_disk_space_gb: Minimum required disk size in GB

    Raises:
        RuntimeError: If no disks are available
    """
    eligible = [d for d in disks if d.size_gb is None or d.size_gb >= min_disk_space_gb]
    skipped = [d for d in disks if d.size_gb is not None and d.size_gb < min_disk_space_gb]
    for d in skipped:
        print(f"Skipping {d.path} ({d.size_gb:.1f}GB) — below minimum {min_disk_space_gb:.1f}GB")
    disks = eligible
    print(f"Detected unused whole disks: {[disk.path for disk in disks]}")

    if len(disks) == 0:
        raise RuntimeError("No unused disks and no LVM free space available. Unable to configure storage automatically.")
    elif len(disks) == 1:
        # Single disk setup
        disk_path = disks[0].path
        print(f"Using single disk: {disk_path}")

        if yes_no_prompt("Do you want to proceed?", default=True) is False:
            print("Operation cancelled by user.")    
            return None  # Not an error, just cancelled

        create_filesystem(disk_path)
        mount_media_disk(disk_path, CLOUDRIFT_MEDIA_MOUNT)
        add_to_fstab(disk_path, CLOUDRIFT_MEDIA_MOUNT)
        reload_daemon()
        print(f"Successfully configured single disk at {CLOUDRIFT_MEDIA_MOUNT}")
    elif check_disk_type(disks, DiskType.NVME):
        # Multiple disks - create RAID
        print(f"Multiple NVMe disks detected: {[disk.path for disk in disks]}")
        if yes_no_prompt("Do you want to proceed?", default=True) is False:
            print("Operation cancelled by user.")    
            return None  # Not an error, just cancelled
        create_raid_array(disks)
        create_filesystem("/dev/md0")
        mount_media_disk("/dev/md0", CLOUDRIFT_MEDIA_MOUNT)
        add_to_fstab("/dev/md0", CLOUDRIFT_MEDIA_MOUNT)
        reload_daemon()
        print(f"Successfully configured RAID array at {CLOUDRIFT_MEDIA_MOUNT}")
    else:
        print("No valid disk configuration found (e.g. multiple NVMe disks for RAID). Please configure disks manually.")
        print("Root mount point will be used for storage. Consider adding more disks or freeing up LVM space for better performance and capacity.")
        print("Found disks:")
        for disk in disks:
            print(f"  - {disk.path} (type={disk.type}, rota={disk.rota}, tran={disk.tran})")

def configure_disks(min_disk_space_gb: float = 10.0):
    """
    Configure disks for CloudRift storage.
    Checks for LVM free space first, then falls back to regular disks.

    Args:
        min_disk_space_gb: Minimum required free/disk space in GB to consider a disk or LVM VG eligible.
    """
    # Validate dependencies we directly call
    for bin_name in ("lsblk", "systemctl", "bash", "vgs", "lvcreate"):
        if shutil.which(bin_name) is None:
            raise RuntimeError(f"Missing required command: {bin_name}")

    print(f"Minimum required disk space: {min_disk_space_gb:.1f}GB")

    # First, check if there's free space in LVM
    lvm_info = get_lvm_free_space(min_disk_space_gb)

    if lvm_info:
        # Use LVM free space
        vg_name, free_gb = lvm_info
        configure_lvm_storage(vg_name, free_gb)
    else:
        # No LVM free space, check for unused disks
        disks = find_unused_whole_disks()
        configure_regular_disks(disks, min_disk_space_gb)

class ConfigureDisksCmd(BaseCmd):
    """ Command to configure disks. """

    def name(self) -> str:
        return "Configure Disks"
    
    def description(self) -> str:
        return "Configures disks for use with LVM and RAID."

    def execute(self, env: Dict[str, Any]) -> bool:
        try:
            if os.path.exists(CLOUDRIFT_MEDIA_MOUNT):
                print(f"{CLOUDRIFT_MEDIA_MOUNT} already exists, skipping disk configuration.")
                return True
            min_disk_space_gb = float(env.get("min_disk_space_gb", MIN_DISK_SPACE_GB))
            configure_disks(min_disk_space_gb)
            return True
        except Exception as e:
            print(f"Error configuring disks: {e}")
            return False