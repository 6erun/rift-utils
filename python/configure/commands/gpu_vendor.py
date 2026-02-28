import subprocess
from typing import Any, Dict, Optional
from .cmd import BaseCmd

GPU_VENDORS = {
    "nvidia": {
        "pci_vendor_hex": "0x10de",
        "pci_vendor_short": "10de",
        "lspci_pattern": "NVIDIA Corporation",
        "modules_to_blacklist": "nouveau,nvidia,nvidiafb,snd_hda_intel",
        "management_tool": "nvidia-smi",
        "udev_rule_file": "/etc/udev/rules.d/99-vfio-nvidia-power.rules",
    },
    "amd": {
        "pci_vendor_hex": "0x1002",
        "pci_vendor_short": "1002",
        "lspci_pattern": "Advanced Micro Devices",
        "modules_to_blacklist": "amdgpu,radeon,snd_hda_intel",
        "management_tool": "rocm-smi",
        "udev_rule_file": "/etc/udev/rules.d/99-vfio-amd-power.rules",
    },
}


def detect_gpu_vendor() -> Optional[str]:
    """
    Scans lspci -nnk output to detect whether NVIDIA or AMD GPUs are present.
    Returns 'nvidia', 'amd', or None if no supported GPU is found.
    """
    try:
        output = subprocess.check_output(['lspci', '-nnk'], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error running lspci: {e}")
        return None

    has_nvidia = False
    has_amd = False

    for line in output.splitlines():
        if 'NVIDIA Corporation' in line and ('VGA' in line or 'Display' in line or '3D' in line):
            has_nvidia = True
        if 'Advanced Micro Devices' in line and ('VGA' in line or 'Display' in line or '3D' in line):
            has_amd = True

    if has_nvidia and has_amd:
        print("Warning: Both NVIDIA and AMD GPUs detected. Defaulting to NVIDIA.")
        return "nvidia"
    elif has_nvidia:
        return "nvidia"
    elif has_amd:
        return "amd"
    else:
        return None


class DetectGpuVendorCmd(BaseCmd):
    """Command to detect GPU vendor and populate env with vendor-specific constants."""

    def name(self) -> str:
        return "Detect GPU Vendor"

    def description(self) -> str:
        return "Detects whether NVIDIA or AMD GPUs are present and sets vendor-specific configuration."

    def execute(self, env: Dict[str, Any]) -> bool:
        # Allow workflow to force vendor via environment
        vendor = env.get('gpu_vendor')

        if vendor and vendor in GPU_VENDORS:
            print(f"GPU vendor forced via environment: {vendor}")
        else:
            vendor = detect_gpu_vendor()
            if vendor is None:
                print("Error: No supported GPU vendor detected.")
                return False
            print(f"Detected GPU vendor: {vendor}")

        config = GPU_VENDORS[vendor]
        env['gpu_vendor'] = vendor
        env['gpu_pci_vendor_hex'] = config['pci_vendor_hex']
        env['gpu_pci_vendor_short'] = config['pci_vendor_short']
        env['gpu_lspci_pattern'] = config['lspci_pattern']
        env['gpu_modules_to_blacklist'] = config['modules_to_blacklist']
        env['gpu_management_tool'] = config['management_tool']
        env['gpu_udev_rule_file'] = config['udev_rule_file']

        print(f"  PCI vendor hex: {config['pci_vendor_hex']}")
        print(f"  lspci pattern: {config['lspci_pattern']}")
        print(f"  Modules to blacklist: {config['modules_to_blacklist']}")
        print(f"  Management tool: {config['management_tool']}")
        print(f"  Udev rule file: {config['udev_rule_file']}")
        return True
