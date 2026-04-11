# CLAUDE.md — rift-utils

## Project Overview

Collection of scripts and tools to provision and configure a bare-metal server node so it can connect to the **CloudRift** datacenter service. The main concerns are:

- Installing NVIDIA drivers, Docker, and the NVIDIA container toolkit
- Setting up QEMU/KVM virtualization (libvirt, VFIO/IOMMU, grub, initramfs)
- Configuring GPU power, memory (hugepages), and disk (RAID) settings
- Installing the CloudRift service (`rift`) and CLI on the node

## Repository Layout

```
python/
  configure/
    configure.py          # Main entry point — workflow/command runner
    commands/             # Auto-discovered BaseCmd subclasses (one file per concern)
    workflows/            # Built-in YAML workflows (vm-and-docker-setup, vm-only-setup, test)
  list_instance_instructions.py
  requirements.txt        # pytest, PyYAML, black, flake8, mypy
  tests/
    test_configure_libvirt.py

scripts/
  client_setup.sh         # Shell script alternative: installs docker/nvidia/rift/vm packages
  install-script-*.sh     # Individual component installers (cuda, drivers, docker, etc.)
  node_info.py
  check_pci_state.sh
  vm-backups/             # VM backup/restore helpers

Makefile                  # Top-level entry point: `sudo make configure`
```

## Architecture: Commands and Workflows

### BaseCmd

All configuration steps are `BaseCmd` subclasses in `python/configure/commands/`. Each must implement:

- `name() -> str` — display name (defaults to class name)
- `description() -> str` — one-line description
- `execute(env: Dict[str, Any]) -> bool` — performs the step; returns `True` on success

Commands are **auto-discovered** at import time via `pkgutil` in `commands/__init__.py`. No registration needed — just create the file and inherit from `BaseCmd`.

### Workflows

Workflows are YAML files that list commands by class name with optional `environment` dicts:

```yaml
name: "My Workflow"
description: "..."
commands:
  - name: "AptInstallCmd"
    environment:
      packages:
        - "qemu-kvm"
  - name: "InstallNvidiaDriverCmd"
```

Workflows are loaded from `python/configure/workflows/` automatically.

### Adding a New Command

1. Create `python/configure/commands/my_command.py`
2. Subclass `BaseCmd` and implement `name()`, `description()`, `execute()`
3. No further registration — it will be auto-discovered

## Running the Configurator

```bash
# Recommended (handles venv + deps automatically)
sudo make configure

# Manual
sudo python3 python/configure/configure.py

# List available workflows / commands
python/configure/configure.py --list-workflows
python/configure/configure.py --list-commands

# Run a specific workflow or command
sudo python/configure/configure.py --workflow "VM and Docker Configuration"
sudo python/configure/configure.py --yaml-workflow path/to/custom.yaml
sudo python/configure/configure.py --command "InstallNvidiaDriverCmd"
```

All configure commands require `root` (checked via `os.geteuid()`).

## Shell Script Alternative

```bash
# Install all components
sudo ./scripts/client_setup.sh

# Install only one component
sudo ./scripts/client_setup.sh --only=nvidia --nvidia-driver-version=570-server
# Components: docker | nvidia | driver | rift | vm
```

## Development

### Install dependencies

```bash
make install          # creates venv + installs python/requirements.txt
# or manually:
pip install -r python/requirements.txt
```

### Run tests

```bash
cd python && pytest
# or with coverage:
cd python && pytest --cov=configure
```

Tests live in `python/tests/`. The test suite uses `pytest-mock` and patches file paths — **do not use real filesystem paths in tests**.

### Linting / formatting

```bash
black python/
flake8 python/
mypy python/
```

## Key Conventions

- All system-modifying commands require `sudo` / root.
- The `env` dict passed to `execute()` is shared across all commands in a workflow — use it to pass data between steps (e.g., GPU PCI IDs read by `GetGpuPciIdsCmd` and consumed by `AddGrubVirtualizationOptionsCmd`).
- YAML workflows use `environment:` (not `params:` or `args:`) for per-command config.
- After a full workflow run the script prompts for reboot; individual `--command` runs do not.
- Scripts in `scripts/` are standalone shell scripts and do not depend on the Python layer.
