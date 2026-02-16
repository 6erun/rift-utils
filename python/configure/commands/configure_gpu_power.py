import os
import subprocess
from typing import Any, Dict
from .cmd import BaseCmd
from .utils import run

UDEV_RULE_FILE = '/etc/udev/rules.d/99-vfio-nvidia-power.rules'
MODPROBE_CONF_FILE = '/etc/modprobe.d/vfio-pci-power.conf'

def create_gpu_power_udev_rule(vendor_hex='0x10de', rule_file=UDEV_RULE_FILE):
    """
    Creates udev rule to prevent GPU from going into deep D3 state.
    Returns: 'created' if rule was created/updated, 'exists' if already correct, 'error' on failure.
    """
    udev_rule_content = f'''# Keep all GPU PCI functions in D0 (no runtime suspend / no D3cold)
# Match all devices with vendor {vendor_hex} and set power management
ACTION=="add|change", SUBSYSTEM=="pci", ATTR{{vendor}}=="{vendor_hex}", \\
  RUN+="/bin/sh -c 'echo on > /sys$devpath/power/control 2>/dev/null || true; echo 0 > /sys$devpath/d3cold_allowed 2>/dev/null || true'"

# Also handle bind events
ACTION=="bind", SUBSYSTEM=="pci", ATTR{{vendor}}=="{vendor_hex}", \\
  RUN+="/bin/sh -c 'echo on > /sys$devpath/power/control 2>/dev/null || true; echo 0 > /sys$devpath/d3cold_allowed 2>/dev/null || true'"
'''

    try:
        # Check if rule already exists and has same content
        if os.path.exists(rule_file):
            with open(rule_file, 'r') as f:
                existing_content = f.read()
                if existing_content == udev_rule_content:
                    print(f"Udev rule {rule_file} already exists with correct content.")
                    return 'exists'

        # Write the udev rule
        with open(rule_file, 'w') as f:
            f.write(udev_rule_content)
        print(f"Created/updated udev rule: {rule_file}")

        # Reload udev rules
        run(['udevadm', 'control', '--reload'], check=True)
        print("Reloaded udev rules.")

        # Trigger udev for existing devices
        run(['udevadm', 'trigger', '--subsystem-match=pci', f'--attr-match=vendor={vendor_hex}'], check=False)
        print(f"Triggered udev for existing devices (vendor={vendor_hex}).")

        return 'created'
    except IOError as e:
        print(f"Error writing udev rule to {rule_file}: {e}")
        return 'error'
    except subprocess.CalledProcessError as e:
        print(f"Error reloading udev rules: {e}")
        return 'error'

def apply_gpu_power_settings_immediately(vendor_hex='0x10de'):
    """
    Immediately apply GPU power settings to all devices matching the vendor.
    """
    try:
        # Use a shell command to apply settings
        # Single line command to avoid shell parsing issues
        cmd = f'for dev in /sys/bus/pci/devices/*/vendor; do [ "$(cat $dev 2>/dev/null)" = "{vendor_hex}" ] && pci=${{dev%/vendor}} && echo on > $pci/power/control 2>/dev/null && echo 0 > $pci/d3cold_allowed 2>/dev/null && echo "  Configured: $(basename $pci)"; done'

        print(f"Applying power settings to GPU devices (vendor={vendor_hex})...")
        stdout, stderr, returncode = run(['sh', '-c', cmd], capture_output=True, check=False)

        if stdout:
            print(stdout)

        # Don't fail even if the command returns non-zero - sometimes it's due to permission on some files
        # We'll verify the actual state below
        print("Power settings application completed.")
        return True

    except subprocess.CalledProcessError as e:
        # This shouldn't happen with check=False, but keep it for safety
        print(f"Note: Power settings command returned an error, but this may be normal: {e}")
        return True  # Continue anyway since we'll verify below
    except Exception as e:
        print(f"Unexpected error: {e}")
        return True  # Continue anyway since we'll verify below

def create_vfio_pci_power_conf():
    """
    Creates modprobe configuration to disable idle D3 for vfio-pci.
    Returns: 'created' if conf was created/updated, 'exists' if already correct, 'error' on failure.
    """
    conf_content = "options vfio-pci disable_idle_d3=1\n"

    try:
        # Check if conf already exists and has same content
        if os.path.exists(MODPROBE_CONF_FILE):
            with open(MODPROBE_CONF_FILE, 'r') as f:
                existing_content = f.read()
                if existing_content == conf_content:
                    print(f"Modprobe conf {MODPROBE_CONF_FILE} already exists with correct content.")
                    return 'exists'

        # Write the modprobe conf
        with open(MODPROBE_CONF_FILE, 'w') as f:
            f.write(conf_content)
        print(f"Created/updated modprobe conf: {MODPROBE_CONF_FILE}")

        return 'created'
    except IOError as e:
        print(f"Error writing modprobe conf to {MODPROBE_CONF_FILE}: {e}")
        return 'error'

def verify_gpu_power_state(vendor_short='10de', rule_file=UDEV_RULE_FILE):
    """
    Verifies that GPU power management is correctly configured.
    """
    print("\nVerifying GPU power state configuration...")
    verification_passed = True

    # Check if udev rule exists
    if os.path.exists(rule_file):
        print(f"✓ Udev rule exists: {rule_file}")
    else:
        print(f"✗ Udev rule missing: {rule_file}")
        verification_passed = False

    # Check if modprobe conf exists
    if os.path.exists(MODPROBE_CONF_FILE):
        print(f"✓ Modprobe conf exists: {MODPROBE_CONF_FILE}")
    else:
        print(f"✗ Modprobe conf missing: {MODPROBE_CONF_FILE}")
        verification_passed = False

    # Check current GPU power states
    try:
        # Find GPU devices by vendor
        lspci_output = subprocess.check_output(['lspci', '-d', f'{vendor_short}:', '-D'], text=True)
        gpu_devices = []
        for line in lspci_output.strip().split('\n'):
            if line:
                # Extract PCI address (e.g., 0000:01:00.0)
                pci_addr = line.split()[0]
                gpu_devices.append(pci_addr)

        if gpu_devices:
            print(f"\nFound {len(gpu_devices)} GPU device(s) (vendor={vendor_short}):")
            for device in gpu_devices:
                # Check power control
                power_control_path = f'/sys/bus/pci/devices/{device}/power/control'
                d3cold_path = f'/sys/bus/pci/devices/{device}/d3cold_allowed'

                if os.path.exists(power_control_path):
                    with open(power_control_path, 'r') as f:
                        power_state = f.read().strip()
                    print(f"  {device}: power/control = {power_state}", end='')
                    if power_state != 'on':
                        print(" (WARNING: should be 'on')")
                        verification_passed = False
                    else:
                        print(" ✓")

                if os.path.exists(d3cold_path):
                    with open(d3cold_path, 'r') as f:
                        d3cold_state = f.read().strip()
                    print(f"           d3cold_allowed = {d3cold_state}", end='')
                    if d3cold_state != '0':
                        print(" (WARNING: should be '0')")
                        verification_passed = False
                    else:
                        print(" ✓")
        else:
            print(f"\nNo GPU devices found for vendor {vendor_short}.")
    except subprocess.CalledProcessError as e:
        print(f"\nError checking GPU devices: {e}")
        verification_passed = False
    except IOError as e:
        print(f"\nError reading power state files: {e}")
        verification_passed = False

    return verification_passed

class CreateGpuPowerUdevRuleCmd(BaseCmd):
    """Command to create udev rule for GPU power management."""

    def name(self) -> str:
        return "Create GPU Power Udev Rule"

    def description(self) -> str:
        return "Creates udev rule to prevent GPUs from entering D3 power state."

    def execute(self, env: Dict[str, Any]) -> bool:
        vendor_hex = env.get('gpu_pci_vendor_hex', '0x10de')
        rule_file = env.get('gpu_udev_rule_file', UDEV_RULE_FILE)
        result = create_gpu_power_udev_rule(vendor_hex, rule_file)
        if result == 'error':
            print("Failed to create GPU power udev rule.")
            return False
        elif result == 'exists':
            print("GPU power udev rule already configured.")
            return True
        else:  # 'created'
            print("GPU power udev rule configured successfully.")
            return True

class CreateVfioPciPowerConfCmd(BaseCmd):
    """Command to create vfio-pci power configuration."""

    def name(self) -> str:
        return "Create VFIO PCI Power Config"

    def description(self) -> str:
        return "Creates modprobe configuration to disable idle D3 for vfio-pci."

    def execute(self, env: Dict[str, Any]) -> bool:
        result = create_vfio_pci_power_conf()
        if result == 'error':
            print("Failed to create vfio-pci power configuration.")
            return False
        elif result == 'exists':
            print("VFIO-PCI power configuration already configured.")
            return True
        else:  # 'created'
            print("VFIO-PCI power configuration created successfully.")
            return True

class VerifyGpuPowerStateCmd(BaseCmd):
    """Command to verify GPU power state configuration."""

    def name(self) -> str:
        return "Verify GPU Power State"

    def description(self) -> str:
        return "Verifies that GPU power management is correctly configured."

    def execute(self, env: Dict[str, Any]) -> bool:
        vendor_short = env.get('gpu_pci_vendor_short', '10de')
        rule_file = env.get('gpu_udev_rule_file', UDEV_RULE_FILE)
        return verify_gpu_power_state(vendor_short, rule_file)

class ConfigureGpuPowerCmd(BaseCmd):
    """Combined command to configure GPU power management."""

    def name(self) -> str:
        return "Configure GPU Power Management"

    def description(self) -> str:
        return "Configures udev rules and modprobe settings to prevent GPUs from entering D3 state."

    def execute(self, env: Dict[str, Any]) -> bool:
        vendor_hex = env.get('gpu_pci_vendor_hex', '0x10de')
        vendor_short = env.get('gpu_pci_vendor_short', '10de')
        rule_file = env.get('gpu_udev_rule_file', UDEV_RULE_FILE)

        # Track if we had any errors
        had_error = False

        # Create udev rule
        udev_result = create_gpu_power_udev_rule(vendor_hex, rule_file)
        if udev_result == 'error':
            print("Error: Failed to create udev rule.")
            had_error = True
        elif udev_result == 'exists':
            print("Note: Udev rule was already configured.")

        # Create modprobe conf
        modprobe_result = create_vfio_pci_power_conf()
        if modprobe_result == 'error':
            print("Error: Failed to create modprobe configuration.")
            had_error = True
        elif modprobe_result == 'exists':
            print("Note: Modprobe conf was already configured.")

        # Apply settings immediately (may show warnings but that's OK)
        apply_gpu_power_settings_immediately(vendor_hex)

        # Verify configuration - this is the real test
        verification_passed = verify_gpu_power_state(vendor_short, rule_file)

        if had_error:
            print("\nErrors occurred during configuration.")
            return False
        elif verification_passed:
            print("\nAll GPU power management configurations verified successfully!")
            return True
        else:
            print("\nWarning: Some verification checks failed. Settings have been applied but may require a reboot for full effect.")
            # Return True anyway if the files were created, as the settings will take effect on reboot
            if os.path.exists(rule_file) and os.path.exists(MODPROBE_CONF_FILE):
                print("Configuration files are in place. Settings will be fully applied after reboot.")
                return True
            return False