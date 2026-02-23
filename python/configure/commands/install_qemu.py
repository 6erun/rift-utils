
import os
import re
import subprocess
from typing import Any, Dict
from .cmd import BaseCmd
from .utils import run


# Minimum required versions.
MIN_QEMU_VERSION = (9, 0)
MIN_LIBVIRT_VERSION = (10, 6)

# Canonical Server Backports PPA (supports Jammy and Noble).
BACKPORTS_PPA = "ppa:canonical-server/server-backports"

# Packages to upgrade.
QEMU_PACKAGES = [
    "qemu-system-x86",
    "qemu-block-extra",
    "qemu-system-common",
    "qemu-system-data",
    "qemu-system-gui",
    "qemu-utils",
]

LIBVIRT_PACKAGES = [
    "libvirt-daemon-system",
    "libvirt-daemon-driver-qemu",
    "libvirt-clients",
]


def parse_qemu_version(version_str: str) -> tuple[int, int]:
    """Parse QEMU version like '9.0.2' or 'QEMU emulator version 9.0.2 ...' into (9, 0)."""
    match = re.search(r"(\d+)\.(\d+)", version_str)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return (0, 0)


def parse_libvirt_version(version_str: str) -> tuple[int, int]:
    """Parse libvirt version like '10.6.0' into (10, 6)."""
    match = re.search(r"(\d+)\.(\d+)", version_str)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return (0, 0)


def get_installed_qemu_version() -> str | None:
    """Return the installed QEMU version string, or None."""
    output, _, rc = run(
        ["qemu-system-x86_64", "--version"],
        capture_output=True, check=False, quiet_stderr=True,
    )
    if rc != 0:
        return None
    return output.strip()


def get_installed_libvirt_version() -> str | None:
    """Return the installed libvirt version string, or None."""
    output, _, rc = run(
        ["virsh", "--version"],
        capture_output=True, check=False, quiet_stderr=True,
    )
    if rc != 0:
        return None
    return output.strip()


def qemu_needs_upgrade() -> bool:
    """Check if installed QEMU is older than the minimum required version."""
    installed = get_installed_qemu_version()
    if installed is None:
        print("QEMU is not installed.")
        return True
    version = parse_qemu_version(installed)
    print(f"Installed QEMU version: {version[0]}.{version[1]}")
    return version < MIN_QEMU_VERSION


def libvirt_needs_upgrade() -> bool:
    """Check if installed libvirt is older than the minimum required version."""
    installed = get_installed_libvirt_version()
    if installed is None:
        print("libvirt is not installed.")
        return True
    version = parse_libvirt_version(installed)
    print(f"Installed libvirt version: {version[0]}.{version[1]}")
    return version < MIN_LIBVIRT_VERSION


def is_ppa_configured() -> bool:
    """Check if the Canonical Server Backports PPA is already configured."""
    output, _, rc = run(
        ["grep", "-r", "canonical-server/server-backports",
         "/etc/apt/sources.list.d/"],
        capture_output=True, check=False, quiet_stderr=True,
    )
    return rc == 0 and len(output) > 0


def add_backports_ppa() -> None:
    """Add the Canonical Server Backports PPA."""
    if is_ppa_configured():
        print("Canonical Server Backports PPA is already configured.")
        return
    print("Adding Canonical Server Backports PPA...")
    run(["add-apt-repository", "-y", BACKPORTS_PPA])


def upgrade_packages(packages: list[str]) -> None:
    """Upgrade the specified packages from the backports PPA."""
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "update", "-qq"])
    run([
        "apt-get", "install", "-y",
        "-o", "Dpkg::Options::=--force-confold",
    ] + packages)


class UpgradeQemuCmd(BaseCmd):
    """Command to upgrade QEMU and libvirt for modern GPU passthrough."""

    def name(self) -> str:
        return "Upgrade QEMU and libvirt"

    def description(self) -> str:
        qemu_str = f"{MIN_QEMU_VERSION[0]}.{MIN_QEMU_VERSION[1]}"
        libvirt_str = f"{MIN_LIBVIRT_VERSION[0]}.{MIN_LIBVIRT_VERSION[1]}"
        return (
            f"Ensures QEMU >= {qemu_str} and libvirt >= {libvirt_str} "
            f"are installed for reliable GPU passthrough with modern GPUs."
        )

    def execute(self, env: Dict[str, Any]) -> bool:
        need_qemu = qemu_needs_upgrade()
        need_libvirt = libvirt_needs_upgrade()

        if not need_qemu and not need_libvirt:
            print("QEMU and libvirt are already sufficient. Skipping.")
            return True

        packages = []
        if need_qemu:
            packages.extend(QEMU_PACKAGES)
        if need_libvirt:
            packages.extend(LIBVIRT_PACKAGES)

        try:
            add_backports_ppa()
            print(f"Upgrading packages: {packages}")
            upgrade_packages(packages)

            # Restart libvirtd to pick up the new version.
            print("Restarting libvirtd...")
            run(["systemctl", "restart", "libvirtd"])

            # Verify.
            qemu_ver = get_installed_qemu_version()
            libvirt_ver = get_installed_libvirt_version()
            print(f"QEMU after upgrade: {qemu_ver}")
            print(f"libvirt after upgrade: {libvirt_ver}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to upgrade QEMU/libvirt: {e}")
            return False
