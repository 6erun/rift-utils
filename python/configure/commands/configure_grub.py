from .cmd import BaseCmd
from .utils import run, write_file
from typing import Dict, Any
import os
import re
import subprocess

GRUB_MAIN_FILE = '/etc/default/grub'
GRUB_D_DIR = '/etc/default/grub.d'
VFIO_GRUB_FILE = os.path.join(GRUB_D_DIR, '99-cloudrift.cfg')

def update_grub():
    """
    Updates GRUB configuration by running 'update-grub'.
    """
    print("Updating GRUB configuration...")
    run(['update-grub'], check=True)
    print("GRUB configuration updated.")

def read_options_from_file(file_path, param_name):
    all_options = []
    with open(file_path, 'r') as f:
        for line in f:
            if param_name in line:
                match = re.search(param_name + r'="([^"]*)"', line)
                if match:
                    all_options.extend(match.group(1).split())
    return all_options

def get_existing_grub_parameters(param_name):
    """
    Reads the param_name from /etc/default/grub and any
    overrides in /etc/default/grub.d.

    Returns:
        A string containing all existing kernel parameters.
    """
    all_options = []

    # Read from the main GRUB file
    try:
        all_options = read_options_from_file(GRUB_MAIN_FILE, param_name)
    except FileNotFoundError:
        print(f"Warning: {GRUB_MAIN_FILE} not found. Starting with an empty command line.")

    # Read from override files in grub.d
    if os.path.exists(GRUB_D_DIR):
        for filename in sorted(os.listdir(GRUB_D_DIR)):
            if filename.endswith('.cfg'):
                filepath = os.path.join(GRUB_D_DIR, filename)
                try:
                    all_options.extend(read_options_from_file(filepath, param_name))
                except IOError as e:
                    print(f"Warning: Could not read {filepath}: {e}")

    # Deduplicate and return as a list
    unique_options = sorted(list(set(all_options)))
    return unique_options

def create_grub_override(grub_options: Dict[str, Any]) -> bool:
    """
    Creates a new GRUB configuration file in /etc/default/grub.d with the
    necessary kernel parameters, appending to existing ones.

    Args:
        grub_options: A dictionary of kernel parameters with options to add.
    """

    if os.path.exists(VFIO_GRUB_FILE):
        print(f"Warning: {VFIO_GRUB_FILE} already exists and will be overwritten.")
        # Check existing options in the file
        existing_options = {}
        if os.path.exists(VFIO_GRUB_FILE):
            try:
                with open(VFIO_GRUB_FILE, 'r') as f:
                    for line in f:
                        match = re.search(r'([A-Z_]+)="([^"]*)"', line)
                        if match:
                            existing_options[match.group(1)] = match.group(2)
                
                # Compare existing with new options
                if existing_options == grub_options:
                    print("GRUB options are already up to date. No changes needed.")
                    return False
                else:
                    print("Existing options differ from new options:")
                    for k in set(existing_options.keys()) | set(grub_options.keys()):
                        if k in existing_options and k in grub_options:
                            print(f"  {k}: '{existing_options[k]}' -> '{grub_options[k]}'")
                        elif k in grub_options:
                            print(f"  {k}: (none) -> '{grub_options[k]}'")
                        else:
                            print(f"  {k}: '{existing_options[k]}' -> (removed)")
            except IOError as e:
                print(f"Warning: Could not read existing file {VFIO_GRUB_FILE}: {e}")

    # grub_d_content = f'GRUB_CMDLINE_LINUX_DEFAULT="{final_options_str}"\n'

    grub_d_content = "\n".join([f'{key}="{grub_options[key]}"' for key in grub_options])

    print(f"Creating override file {VFIO_GRUB_FILE}...")
    print(f"Adding line: {grub_d_content}")

    if not os.path.exists(GRUB_D_DIR):
        os.makedirs(GRUB_D_DIR)

    try:
        write_file(VFIO_GRUB_FILE, grub_d_content)
        print("GRUB override file created successfully.")
        return True
    except IOError as e:
        print(f"Error writing to {VFIO_GRUB_FILE}: {e}")
        return False
class ReadGrubCmd(BaseCmd):
    """ Command to read existing GRUB parameters. """

    def name(self) -> str:
        return "Read GRUB Parameters"
    
    def description(self) -> str:
        return "Reads existing GRUB parameters from the system."

    def execute(self, env: Dict[str, Any]) -> bool:
        env['GRUB_CMDLINE_LINUX_DEFAULT'] = get_existing_grub_parameters('GRUB_CMDLINE_LINUX_DEFAULT')
        env['GRUB_CMDLINE_LINUX'] = get_existing_grub_parameters('GRUB_CMDLINE_LINUX')
        print(f"Existing GRUB_CMDLINE_LINUX_DEFAULT: {env['GRUB_CMDLINE_LINUX_DEFAULT']}")
        print(f"Existing GRUB_CMDLINE_LINUX: {env['GRUB_CMDLINE_LINUX']}")
        return True

class GetIommuTypeCmd(BaseCmd):
    """ Command to get the IOMMU type. """

    def name(self) -> str:
        return "Get IOMMU Type"
    
    def description(self) -> str:
        return "Determines the IOMMU type of the system."

    def execute(self, env: Dict[str, Any]) -> bool:
        iommu_type = 'intel_iommu=on'
        if 'AMD' in open('/proc/cpuinfo').read():
            iommu_type = 'amd_iommu=on'
        print(f"Detected CPU type, using '{iommu_type}'.")
        env['IOMMU_TYPE'] = iommu_type
        return True

class GetGpuPciIdsCmd(BaseCmd):
    """ Command to get GPU PCI IDs. """

    def name(self) -> str:
        return "Get GPU PCI IDs"
    
    def description(self) -> str:
        return "Retrieves the PCI IDs of the GPUs in the system."

    def execute(self, env: Dict[str, Any]) -> bool:
        try:
            lspci_output = subprocess.check_output(['lspci', '-nnk']).decode('utf-8')
            nvidia_lines = [line for line in lspci_output.splitlines() if 'NVIDIA Corporation' in line]

            if not nvidia_lines:
                print("No NVIDIA GPUs found.")
                return False

            pci_ids = []
            for line in nvidia_lines:
                match = re.search(r'\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]', line)
                if match:
                    vendor_id = match.group(1)
                    device_id = match.group(2)
                    pci_ids.append(f"{vendor_id}:{device_id}")

            env['GPU_PCI_IDS'] = sorted(list(set(pci_ids)))
            print(f"Detected GPU PCI IDs: {env['GPU_PCI_IDS']}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error running lspci: {e}")
            return False


class AddGrubVirtualizationOptionsCmd(BaseCmd):
    """ Command to add virtualization options to GRUB. """

    def name(self) -> str:
        return "Add Virtualization Options to GRUB"
    
    def description(self) -> str:
        return "Adds necessary virtualization options to GRUB configuration."

    def execute(self, env: Dict[str, Any]) -> bool:
        if 'IOMMU_TYPE' not in env or 'GPU_PCI_IDS' not in env or 'GRUB_CMDLINE_LINUX_DEFAULT' not in env:
            print("Error: Missing required environment variables.")
            return False

        iommu_type = env['IOMMU_TYPE']
        pci_ids = env['GPU_PCI_IDS']
        skip_vfio_binding = env.get('skip_vfio_binding', False)

        new_options = [
            # Enables IOMMU passthrough mode for better performance; devices not
            # requiring translation bypass the IOMMU, reducing latency
            'iommu=pt',
            # Fixes PCI BAR allocation failures when GPUs have large memory regions
            # that conflict with other device address ranges.
            # Note: Often unnecessary on kernels 5.4+; try removing if no BAR issues
            'pci=realloc',
            # Disables PCIe Active State Power Management; prevents GPU reset failures
            # and power state transition issues during passthrough.
            # Note: May be unnecessary on modern hardware/BIOS; try removing first
            'pcie_aspm=off',
            # Disables Advanced Error Reporting; prevents kernel log floods from
            # non-fatal errors reported by passed-through devices.
            # Note: Masks errors rather than fixing them; only use if seeing AER spam
            'pci=noaer',
            # Enables IOMMU (Intel VT-d or AMD-Vi); required for device isolation
            # and secure memory access in GPU passthrough.
            # Note: AMD IOMMU is often enabled by default on newer kernels (5.15+)
            iommu_type,
            # Prevents host kernel from initializing GPU framebuffer; avoids conflicts
            # when the GPU is meant to be passed through to a VM
            'nomodeset',
            # Disables EFI framebuffer on the GPU; prevents the host from claiming
            # the GPU's display output before VFIO can bind to it.
            # Note: May be unnecessary if vfio-pci binds early via modprobe config
            'video=efifb:off'
        ]

        if not skip_vfio_binding:
            vfio_ids_str = ','.join(pci_ids)
            if vfio_ids_str:
                new_options.append(f'vfio-pci.ids={vfio_ids_str}')
                new_options.append('modprobe.blacklist=nouveau,nvidia,nvidiafb,snd_hda_intel')
            else:
                print("Warning: No PCI IDs provided for VFIO binding. Skipping VFIO options.")
        else:
            print("Skipping VFIO binding and nvidia blacklist (skip_vfio_binding=True).")

        # Only add options that are not already present
        grub_cmdline = env['GRUB_CMDLINE_LINUX_DEFAULT']
        if isinstance(grub_cmdline, list):
            final_options_list = grub_cmdline.copy()
        else:
            # If it's a string, split it into a list
            final_options_list = grub_cmdline.split() if grub_cmdline else []

        for opt in new_options:
            # Simple check to avoid duplicates for non-vfio options
            if not any(opt.split('=')[0] in existing_opt for existing_opt in final_options_list):
                final_options_list.append(opt)

        # Handle the vfio-pci.ids and modprobe.blacklist options carefully
        # We remove the old ones if they exist and add our new, complete ones
        final_options_list = [opt for opt in final_options_list if not opt.startswith('vfio-pci.ids=')]
        final_options_list = [opt for opt in final_options_list if not opt.startswith('modprobe.blacklist=')]
        if not skip_vfio_binding:
            vfio_ids_str = ','.join(pci_ids)
            if vfio_ids_str:
                final_options_list.append(f'vfio-pci.ids={vfio_ids_str}')
                final_options_list.append('modprobe.blacklist=nouveau,nvidia,nvidiafb,snd_hda_intel')

        env['GRUB_CMDLINE_LINUX_DEFAULT'] = ' '.join(final_options_list)
        print(f"Updated GRUB_CMDLINE_LINUX_DEFAULT: {env['GRUB_CMDLINE_LINUX_DEFAULT']}")
        return True

class RemoveGrubOverrideCmd(BaseCmd):
    """ Command to remove GRUB override file. """

    def name(self) -> str:
        return "Remove GRUB Override"
    
    def description(self) -> str:
        return "Removes the GRUB override file if it exists."

    def execute(self, env: Dict[str, Any]) -> bool:
        if os.path.exists(VFIO_GRUB_FILE):
            try:
                os.remove(VFIO_GRUB_FILE)
                print(f"Removed GRUB override file {VFIO_GRUB_FILE}.")
                update_grub()
                return True
            except OSError as e:
                print(f"Error removing {VFIO_GRUB_FILE}: {e}")
                return False
        else:
            print(f"No GRUB override file {VFIO_GRUB_FILE} found to remove.")
            return True

class CreateGrubOverrideCmd(BaseCmd):
    """ Command to create GRUB override file. """

    def name(self) -> str:
        return "Create GRUB Override"
    
    def description(self) -> str:
        return "Creates a GRUB override file with the updated kernel parameters."

    def execute(self, env: Dict[str, Any]) -> bool:
        if 'GRUB_CMDLINE_LINUX_DEFAULT' not in env or 'GRUB_CMDLINE_LINUX' not in env:
            print("Error: Missing required environment variables.")
            return False

        grub_options = {
            'GRUB_CMDLINE_LINUX_DEFAULT': env['GRUB_CMDLINE_LINUX_DEFAULT'],
            'GRUB_CMDLINE_LINUX': env['GRUB_CMDLINE_LINUX']
        }

        try:
            if create_grub_override(grub_options):
                update_grub()
            return True
        except Exception as e:
            print(f"Error creating GRUB override: {e}")
            return False
