
import subprocess
from typing import Any, Dict
from .cmd import BaseCmd
from .utils import run


# Minimum kernel branch required for reliable VFIO and NVIDIA GPU passthrough.
MIN_KERNEL_BRANCH = (6, 8)

# Package names per Ubuntu release codename.
HWE_PACKAGES = {
    "jammy": [
        "linux-image-generic-hwe-22.04",
        "linux-headers-generic-hwe-22.04",
    ],
}


def get_running_kernel_version() -> str:
    output, _, _ = run(["uname", "-r"], capture_output=True)
    return output.strip()


def parse_kernel_branch(version: str) -> tuple[int, ...]:
    """Extract the (major, minor) tuple from a kernel version string like '6.8.0-100-generic'."""
    parts = version.split(".")
    try:
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return (0, 0)


def kernel_is_sufficient(version: str) -> bool:
    return parse_kernel_branch(version) >= MIN_KERNEL_BRANCH


def is_kernel_branch_installed(branch: tuple[int, ...]) -> bool:
    """Check if a kernel from the target branch is already installed."""
    branch_str = f"{branch[0]}.{branch[1]}"
    output, _, rc = run(
        ["dpkg", "-l", f"linux-image-{branch_str}.*-generic"],
        capture_output=True, check=False, quiet_stderr=True,
    )
    return rc == 0 and "ii" in output


def get_ubuntu_codename() -> str:
    """Return the Ubuntu release codename (e.g. 'jammy', 'noble')."""
    output, _, _ = run(
        ["lsb_release", "-cs"],
        capture_output=True, check=False, quiet_stderr=True,
    )
    return output.strip().lower()


def install_hwe_kernel(codename: str) -> bool:
    """Install the HWE kernel for the given Ubuntu release."""
    packages = HWE_PACKAGES.get(codename)
    if packages is None:
        print(
            f"No HWE kernel override configured for Ubuntu '{codename}'. "
            f"The default kernel on this release should already be sufficient."
        )
        return True

    print(f"Installing HWE kernel packages: {packages}")
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y"] + packages)
    print("HWE kernel installed successfully.")
    return True


class InstallHweKernelCmd(BaseCmd):
    """Command to ensure a kernel >= 6.8 is installed for GPU passthrough."""

    def name(self) -> str:
        return "Install HWE Kernel"

    def description(self) -> str:
        branch_str = f"{MIN_KERNEL_BRANCH[0]}.{MIN_KERNEL_BRANCH[1]}"
        return (
            f"Ensures a kernel >= {branch_str} is installed "
            f"for reliable IOMMU, VFIO, and NVIDIA GPU passthrough support."
        )

    def execute(self, env: Dict[str, Any]) -> bool:
        current = get_running_kernel_version()
        print(f"Current kernel: {current}")

        if kernel_is_sufficient(current):
            print(f"Kernel {current} meets the minimum requirement. Skipping.")
            return True

        if is_kernel_branch_installed(MIN_KERNEL_BRANCH):
            branch_str = f"{MIN_KERNEL_BRANCH[0]}.{MIN_KERNEL_BRANCH[1]}"
            print(
                f"Kernel {branch_str}.x is installed but not running. "
                "A reboot is needed."
            )
            return True

        codename = get_ubuntu_codename()
        print(f"Ubuntu release: {codename}")

        try:
            return install_hwe_kernel(codename)
        except subprocess.CalledProcessError as e:
            print(f"Failed to install HWE kernel: {e}")
            return False
