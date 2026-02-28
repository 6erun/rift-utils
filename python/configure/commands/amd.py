from typing import Any, Dict
from .cmd import BaseCmd
from .utils import run, reboot_prompt, yes_no_prompt


def check_amdgpu():
    """
    Check if the amdgpu kernel driver is loaded.
    """
    output, _, return_code = run("lsmod | grep amdgpu", shell=True, capture_output=True, check=False)
    if return_code == 0 and output:
        print("amdgpu driver is in use.")
    else:
        print("amdgpu driver is not in use.")
    return return_code == 0 and output


def check_amdgpu_installed():
    """
    Check if AMD GPU tools (rocm-smi or amd-smi) are available.
    """
    # Try amd-smi first (newer ROCm)
    output, _, return_code = run("amd-smi version", shell=True, capture_output=True, check=False)
    if return_code == 0 and output:
        print(f"AMD SMI is installed: {output.strip()}")
        return True

    # Fall back to rocm-smi
    output, _, return_code = run("rocm-smi --version", shell=True, capture_output=True, check=False)
    if return_code == 0 and output:
        print(f"ROCm SMI is installed: {output.strip()}")
        return True

    print("AMD GPU management tools are not installed.")
    return False


def check_amdgpu_install_present():
    """
    Check if the amdgpu-install tool is available on the system.
    """
    output, _, return_code = run("which amdgpu-install", shell=True, capture_output=True, check=False)
    return return_code == 0 and output


def remove_amdgpu_driver():
    """
    Remove the AMD GPU driver and associated packages.
    """
    if check_amdgpu():
        print("AMD GPU driver is in use. Attempting to remove it.")

        # Use amdgpu-install --uninstall if available
        if check_amdgpu_install_present():
            print("Running amdgpu-install --uninstall...")
            run(["amdgpu-install", "--uninstall", "-y"], check=False)
        else:
            print("amdgpu-install not found, removing packages manually...")
            run(["apt-get", "remove", "--purge", "-y", "amdgpu-dkms"], check=False)
            run(["apt-get", "autoremove", "-y"], check=False)

        reboot_prompt()
    else:
        print("AMD GPU driver does not appear to be in use.")


def install_amdgpu_driver():
    """
    Install the AMD GPU DKMS driver using amdgpu-install.
    """
    if not check_amdgpu_install_present():
        print("Error: amdgpu-install tool not found.")
        print("Please install it first from: https://www.amd.com/en/support/linux-drivers")
        print("  wget <amdgpu-install .deb URL>")
        print("  sudo apt install ./amdgpu-install_*.deb")
        return False

    print("Installing AMD GPU DKMS driver...")
    _, _, return_code = run(
        ["amdgpu-install", "--usecase=dkms", "-y", "--accept-eula"],
        check=False
    )
    if return_code != 0:
        print("Error: amdgpu-install failed.")
        return False

    print("AMD GPU DKMS driver installed successfully.")
    reboot_prompt()
    return True


def install_rocm():
    """
    Install the ROCm stack using amdgpu-install.
    """
    if not check_amdgpu_install_present():
        print("Error: amdgpu-install tool not found.")
        print("Please install it first from: https://www.amd.com/en/support/linux-drivers")
        return False

    print("Installing ROCm stack...")
    _, _, return_code = run(
        ["amdgpu-install", "--usecase=rocm", "-y", "--accept-eula"],
        check=False
    )
    if return_code != 0:
        print("Error: ROCm installation failed.")
        return False

    print("ROCm stack installed successfully.")
    return True


class RemoveAmdDriverCmd(BaseCmd):
    """Command to remove AMD GPU driver."""

    def name(self) -> str:
        return "Remove AMD GPU Driver"

    def description(self) -> str:
        return "Checks for and removes AMD GPU drivers if they are installed."

    def execute(self, env: Dict[str, Any]) -> bool:
        remove_amdgpu_driver()
        return True


class InstallAmdDriverCmd(BaseCmd):
    """Command to install AMD GPU DKMS driver."""

    def name(self) -> str:
        return "Install AMD GPU Driver"

    def description(self) -> str:
        return "Installs the AMD GPU DKMS driver using amdgpu-install."

    def execute(self, env: Dict[str, Any]) -> bool:
        if check_amdgpu():
            if not yes_no_prompt("AMD GPU driver is already loaded. Do you want to reinstall it?", False):
                return True

        return install_amdgpu_driver()


class InstallRocmCmd(BaseCmd):
    """Command to install ROCm toolkit."""

    def name(self) -> str:
        return "Install ROCm Toolkit"

    def description(self) -> str:
        return "Installs the ROCm toolkit using amdgpu-install."

    def execute(self, env: Dict[str, Any]) -> bool:
        if not check_amdgpu():
            print("AMD GPU driver is not loaded. Please install the driver first.")
            return False

        if check_amdgpu_installed():
            if not yes_no_prompt("ROCm tools are already installed. Do you want to reinstall?", False):
                return True

        return install_rocm()
