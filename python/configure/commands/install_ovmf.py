
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Any, Dict
from .cmd import BaseCmd
from .utils import run


OVMF_DIR = "/usr/share/OVMF"
OVMF_NOBLE_URL = (
    "https://archive.ubuntu.com/ubuntu/pool/main/e/edk2/ovmf_2024.02-2_all.deb"
)

# Minimum required edk2 version (year, month).
MIN_OVMF_VERSION = (2024, 2)

# Files to upgrade from the Noble package.
OVMF_FILES = [
    "OVMF_CODE_4M.fd",
    "OVMF_CODE_4M.secboot.fd",
    "OVMF_VARS_4M.fd",
    "OVMF_VARS_4M.ms.fd",
]

# Symlinks that should point at secboot after install.
OVMF_SYMLINKS = {
    "OVMF_CODE_4M.ms.fd": "OVMF_CODE_4M.secboot.fd",
    "OVMF_CODE_4M.snakeoil.fd": "OVMF_CODE_4M.secboot.fd",
}


def parse_ovmf_version(version_str: str) -> tuple[int, int]:
    """Parse an OVMF/edk2 version like '2024.02-2' into (2024, 2)."""
    match = re.match(r"(\d{4})\.(\d{2})", version_str)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return (0, 0)


def get_installed_ovmf_version() -> str | None:
    """Return the installed ovmf package version, or None."""
    output, _, rc = run(
        ["dpkg-query", "-W", "-f=${Version}", "ovmf"],
        capture_output=True, check=False, quiet_stderr=True,
    )
    if rc != 0:
        return None
    return output.strip()


def ovmf_needs_upgrade() -> bool:
    """Check if the installed OVMF is older than the minimum required version."""
    installed = get_installed_ovmf_version()
    if installed is None:
        print("OVMF package is not installed.")
        return True
    print(f"Installed OVMF package version: {installed}")
    return parse_ovmf_version(installed) < MIN_OVMF_VERSION


def _file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def install_ovmf_from_noble() -> bool:
    """Download the OVMF 2024.02 deb from Noble and install firmware files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        deb_path = os.path.join(tmpdir, "ovmf.deb")
        extract_dir = os.path.join(tmpdir, "ovmf-extract")

        min_str = f"{MIN_OVMF_VERSION[0]}.{MIN_OVMF_VERSION[1]:02d}"
        print(f"Downloading OVMF {min_str} from Ubuntu Noble...")
        urllib.request.urlretrieve(OVMF_NOBLE_URL, deb_path)

        print("Extracting package...")
        os.makedirs(extract_dir)
        run(["dpkg-deb", "-x", deb_path, extract_dir])

        src_ovmf = os.path.join(extract_dir, "usr", "share", "OVMF")
        if not os.path.isdir(src_ovmf):
            print(f"Error: expected {src_ovmf} not found in package.")
            return False

        # Back up and replace each firmware file.
        for fname in OVMF_FILES:
            src = os.path.join(src_ovmf, fname)
            dst = os.path.join(OVMF_DIR, fname)

            if not os.path.exists(src):
                print(f"  Warning: {fname} not found in package, skipping.")
                continue

            if os.path.exists(dst):
                # Skip if identical.
                if _file_md5(src) == _file_md5(dst):
                    print(f"  {fname}: already up to date.")
                    continue
                backup = dst + ".bak"
                print(f"  {fname}: backing up to {backup}")
                shutil.copy2(dst, backup)

            print(f"  {fname}: installing")
            shutil.copy2(src, dst)

        # Ensure each alias is a symlink to its target, matching Noble's layout.
        for link_name, target in OVMF_SYMLINKS.items():
            link_path = os.path.join(OVMF_DIR, link_name)
            target_path = os.path.join(OVMF_DIR, target)
            if not os.path.exists(target_path):
                continue

            if os.path.lexists(link_path):
                # If it's already a symlink that resolves to the same target, keep it.
                if os.path.islink(link_path):
                    real_link = os.path.realpath(link_path)
                    real_target = os.path.realpath(target_path)
                    if real_link == real_target:
                        print(f"  {link_name}: already matches {target}.")
                        continue
                    else:
                        os.remove(link_path)
                else:
                    # Back up existing non-symlink before replacing.
                    backup = link_path + ".bak"
                    print(f"  {link_name}: backing up to {backup}")
                    shutil.copy2(link_path, backup)
                    os.remove(link_path)

            print(f"  {link_name}: creating symlink to {target}")
            os.symlink(target, link_path)

    print(f"OVMF {min_str} firmware files installed successfully.")
    return True


class InstallOvmfCmd(BaseCmd):
    """Command to install OVMF 2024.02+ firmware for modern GPU passthrough."""

    def name(self) -> str:
        return "Install OVMF Firmware"

    def description(self) -> str:
        min_str = f"{MIN_OVMF_VERSION[0]}.{MIN_OVMF_VERSION[1]:02d}"
        return (
            f"Ensures OVMF >= {min_str} is installed for reliable "
            f"UEFI GPU passthrough with modern NVIDIA GPUs."
        )

    def execute(self, env: Dict[str, Any]) -> bool:
        if not os.path.isdir(OVMF_DIR):
            print(f"OVMF directory {OVMF_DIR} does not exist. Installing ovmf package first.")
            run(["apt-get", "update"])
            run(["apt-get", "install", "-y", "ovmf"])

        if not ovmf_needs_upgrade():
            print("OVMF firmware is already sufficient. Skipping.")
            return True

        try:
            return install_ovmf_from_noble()
        except subprocess.CalledProcessError as e:
            print(f"Failed to install OVMF: {e}")
            return False
