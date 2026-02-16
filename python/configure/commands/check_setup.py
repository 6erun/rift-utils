import os
import subprocess
import re
import sys

def check_vfio_driver(lspci_pattern='NVIDIA Corporation'):
    """
    Checks if the VFIO driver is in use for GPUs after reboot.
    """
    print("\nChecking for VFIO driver in use...")
    try:
        lspci_output = subprocess.check_output(['lspci', '-k']).decode('utf-8')
        gpu_pattern = re.escape(lspci_pattern) + r'.*'
        vfio_pattern = r'Kernel driver in use: vfio-pci'

        lines = lspci_output.splitlines()
        found_gpu = False
        for i, line in enumerate(lines):
            if re.search(gpu_pattern, line, re.IGNORECASE):
                found_gpu = True
                print(f"Found GPU device: {line.strip()}")

                # Check the next few lines for the driver
                if i + 1 < len(lines) and re.search(vfio_pattern, lines[i + 1], re.IGNORECASE):
                    print("--> Kernel driver in use: vfio-pci (SUCCESS)")
                elif i + 2 < len(lines) and re.search(vfio_pattern, lines[i + 2], re.IGNORECASE):
                    print("--> Kernel driver in use: vfio-pci (SUCCESS)")
                else:
                    print("--> VFIO driver NOT in use. Check your GRUB configuration.")

        if not found_gpu:
            print(f"No devices matching '{lspci_pattern}' found to check.")

    except subprocess.CalledProcessError as e:
        print(f"Error running lspci: {e}")

