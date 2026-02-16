#!/usr/bin/env python3

import os
import sys
import argparse
import yaml
from typing import Dict, Any, List

from pyparsing import ABC
from commands import get_all_commands, get_command
from commands.cmd import BaseCmd
from commands.utils import numbered_prompt, reboot_prompt, yes_no_prompt
from commands.nvidia import InstallNvidiaContainerToolkitCmd, InstallNvidiaCudaToolkitCmd, InstallNvidiaDriverCmd, RemoveNvidiaDriverCmd
from commands.apt_install import AptInstallCmd
from commands.configure_libvirt import ConfigureLibvirtCmd, CheckVirtualizationCmd
from commands.configure_grub import ReadGrubCmd, GetIommuTypeCmd, GetGpuPciIdsCmd, AddGrubVirtualizationOptionsCmd, CreateGrubOverrideCmd, RemoveGrubOverrideCmd
from commands.configure_initramfs import UpdateInitramfsModulesCmd
from commands.configure_modprobe import CreateVfioConfCmd
from commands.configure_memory import ConfigureMemoryCmd
from commands.configure_disks import ConfigureDisksCmd
from commands.configure_gpu_power import VerifyGpuPowerStateCmd, ConfigureGpuPowerCmd
from commands.configure_docker import ConfigureDockerCmd
from commands.gpu_vendor import DetectGpuVendorCmd
from commands.amd import InstallAmdDriverCmd, InstallRocmCmd

GRUB_MAIN_FILE = '/etc/default/grub'
GRUB_D_DIR = '/etc/default/grub.d'
VFIO_GRUB_FILE = os.path.join(GRUB_D_DIR, '99-cloudrift.cfg')

ALL_COMMANDS = get_all_commands()
WORKFLOWS = [
]

# REQUIRED_PACKAGES = [
#     "qemu-kvm",
#     "libvirt-daemon-system",
#     "genisoimage",
#     "whois",
#     "mdadm"  # RAID devices
# ]

def reboot_server():
    """
    Prompts the user to reboot and provides verification instructions.
    """
    print("\n--- Reboot and Verify ---")
    print("The configuration is complete. You need to reboot for the changes to take effect.")
    print("After rebooting, you can verify the configuration by running the following commands:")
    print("  grep -i huge /proc/meminfo")
    print("  grep hugetlbfs /proc/mounts")
    reboot_prompt()

class WorkflowCommand:
    command: BaseCmd
    environment: Dict[str, Any]

    def __init__(self, command: BaseCmd, environment: Dict[str, Any]):
        self.command = command
        self.environment = environment

class Workflow(ABC):

    commands: list[WorkflowCommand] = []
    environment: Dict[str, Any] = {}

    """
    Abstract base class for configuration commands.
    """
    def name(self) -> str:
        raise NotImplementedError("Subclasses must implement name()")

    def description(self) -> str:
        raise NotImplementedError("Subclasses must implement description()")

    def execute(self, env: Dict[str, Any]) -> bool | None:
        total_commands = len(self.commands)
        print(f"📋 Found {total_commands} configuration command(s) to execute:")
        print("-" * 60)
        
        # Print overview of all commands first
        for i, command in enumerate(self.commands, start=1):
            print(f"  {i}. {command.command.name()}")
            print(f"     └─ {command.command.description()}")
        print("-" * 60)
        print()

        # Prompt for confirmation
        if yes_no_prompt("Do you want to proceed with these changes?", default=True) is False:
            print("Operation cancelled by user.")
            return None  # Not an error, just cancelled

        env = {}  # Shared environment dictionary for commands

        # Execute commands with enhanced output
        for i, command in enumerate(self.commands, start=1):
            print(f"🚀 Step {i}/{total_commands}: {command.command.name()}")
            print(f"📝 Description: {command.command.description()}")
            print(f"⏳ Executing...")
            
            try:
                env.update(command.environment)
                success = command.command.execute(env)
                if success:
                    print(f"✅ Step {i}/{total_commands} completed successfully!")
                else:
                    print(f"❌ Step {i}/{total_commands} failed!")
                    print(f"💥 Command '{command.command.name()}' encountered an error. Exiting.")
                    return False
            except Exception as e:
                print(f"❌ Step {i}/{total_commands} failed with exception!")
                print(f"💥 Error: {str(e)}")
                print(f"🛑 Command '{command.command.name()}' failed. Exiting.")
                return False

            print("-" * 40)
            print()

        print("🎉 All configuration commands completed successfully!")
        print("=" * 60)        
        return True

def load_workflow_from_yaml(file_path: str) -> Workflow:
    """
    Load a workflow from a YAML file.
    Expected YAML format:
    ---
    name: "My Custom Workflow"
    description: "Description of what this workflow does"
    commands:
      - name: "CheckVirtualizationCmd"
      - name: "AptInstallCmd"
        packages:
          - "qemu-kvm"
          - "libvirt-daemon-system"
      - name: "InstallNvidiaDriverCmd"
    """
    try:
        with open(file_path, 'r') as file:
            workflow_data = yaml.safe_load(file)
    except FileNotFoundError:
        raise FileNotFoundError(f"YAML workflow file not found: {file_path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML format in {file_path}: {e}")
    
    if not isinstance(workflow_data, dict):
        raise ValueError(f"YAML file must contain a dictionary at root level")
    
    # Validate required fields
    if 'name' not in workflow_data:
        raise ValueError("YAML workflow must have a 'name' field")
    if 'commands' not in workflow_data:
        raise ValueError("YAML workflow must have a 'commands' field")
    
    workflow_name = workflow_data['name']
    workflow_description = workflow_data.get('description', 'No description provided')
    commands_data = workflow_data['commands']
    
    if not isinstance(commands_data, list):
        raise ValueError("'commands' field must be a list")
    
    # Create command instances from YAML data
    command_instances = []
        
    for cmd_data in commands_data:
        if isinstance(cmd_data, str):
            # Simple string format: just command name
            cmd_name = cmd_data
            cmd_params = {}
        elif isinstance(cmd_data, dict):
            # Dictionary format with potential parameters
            cmd_name = cmd_data.get('name')
            cmd_params = cmd_data.get('environment', {})            
        else:
            raise ValueError(f"Invalid command format: {cmd_data}")
        
        if not cmd_name:
            raise ValueError(f"Command must have a 'name' field: {cmd_data}")
        
        # Find the command class and create instance
        command = get_command(cmd_name)
        if command:
            # Use existing instance from auto-discovery
            command_instances.append(WorkflowCommand(command, cmd_params))
        else:
            raise ValueError(f"Unknown command: {cmd_name}")
    
    # Create a dynamic workflow class
    class YamlWorkflow(Workflow):
        def __init__(self):
            super().__init__()
            self.commands = command_instances
            self._name = workflow_name
            self._description = workflow_description
        
        def name(self) -> str:
            return self._name
        
        def description(self) -> str:
            return self._description
    
    return YamlWorkflow()

def load_workflows(path: str):
    for file in os.listdir(path):
        if file.endswith('.yaml') or file.endswith('.yml'):
            wf = load_workflow_from_yaml(os.path.join(path, file))
            WORKFLOWS.append(wf)

def list_workflows():
    """
    List all available configuration workflows.
    """
    print("=" * 60)
    print("📋 AVAILABLE CONFIGURATION WORKFLOWS")
    print("=" * 60)
    
    for i, workflow in enumerate(WORKFLOWS, start=1):
        print(f"  {i}. {workflow.name()}")
        print(f"     └─ {workflow.description()}")
    print("=" * 60)
    print(f"Total: {len(WORKFLOWS)} workflows available")

def find_workflow(workflow_identifier) -> Workflow | None:
    workflow_to_execute = None

    # Try to parse as index first
    try:
        index = int(workflow_identifier) - 1  # Convert to 0-based index
        if 0 <= index < len(WORKFLOWS):
            workflow_to_execute = WORKFLOWS[index]
    except ValueError:
        # Not a number, try to find by name
        for i, workflow in enumerate(WORKFLOWS):
            if workflow.name().lower() == workflow_identifier.lower():
                workflow_to_execute = workflow
                break

    return workflow_to_execute

def execute_workflow(workflow_identifier):
    """
    Execute a specific workflow by index or name.
    """
    if os.geteuid() != 0:
        print("This script must be run with sudo.")
        sys.exit(1)

    env = {}  # Shared environment dictionary
    workflow_to_execute = find_workflow(workflow_identifier)

    if workflow_to_execute is None:
        print(f"❌ Workflow '{workflow_identifier}' not found.")
        print("Use --list-workflows to see available workflows.")
        sys.exit(1)

    print("=" * 60)
    print(f"🚀 Executing workflow: {workflow_to_execute.name()}")
    print(f"📝 Description: {workflow_to_execute.description()}")
    print(f"⏳ Executing...")
    
    try:
        success = workflow_to_execute.execute(env)
        if success is None:
            print("⚠️ Workflow execution cancelled by user.")
            sys.exit(0)
        elif success:
            print(f"✅ Workflow completed successfully!")
        else:
            print(f"❌ Workflow failed!")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Workflow failed with exception!")
        print(f"💥 Error: {str(e)}")
        sys.exit(1)
    
    print("🎉 Workflow execution completed!")
    print("=" * 60)

    reboot_server()

def execute_yaml_workflow(yaml_file_path: str):
    """
    Execute a workflow defined in a YAML file.
    """
    if os.geteuid() != 0:
        print("This script must be run with sudo.")
        sys.exit(1)

    try:
        workflow = load_workflow_from_yaml(yaml_file_path)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as e:
        print(f"❌ Error loading YAML workflow: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error loading YAML workflow: {e}")
        sys.exit(1)

    print("=" * 60)
    print(f"🚀 Executing YAML workflow: {workflow.name()}")
    print(f"📝 Description: {workflow.description()}")
    print(f"📄 Source: {yaml_file_path}")
    print(f"⏳ Executing...")
    
    try:
        success = workflow.execute({})
        if success is None:
            print("⚠️ Workflow execution cancelled by user.")
            sys.exit(0)
        elif success:
            print(f"✅ YAML workflow completed successfully!")
        else:
            print(f"❌ YAML workflow failed!")
            sys.exit(1)
    except Exception as e:
        print(f"❌ YAML workflow failed with exception!")
        print(f"💥 Error: {str(e)}")
        sys.exit(1)
    
    print("🎉 YAML workflow execution completed!")
    print("=" * 60)

    reboot_server()

def list_commands():
    """
    List all available configuration commands.
    """
    print("=" * 60)
    print("📋 AVAILABLE CONFIGURATION COMMANDS")
    print("=" * 60)

    for i, command in enumerate(ALL_COMMANDS, start=1):
        print(f"  {i}. {command.name()}")
        print(f"     └─ {command.description()}")
    print("=" * 60)
    print(f"Total: {len(ALL_COMMANDS)} commands available")

def execute_specific_command(command_identifier):
    """
    Execute a specific command by index or name.
    """
    if os.geteuid() != 0:
        print("This script must be run with sudo.")
        sys.exit(1)

    env = {}  # Shared environment dictionary
    command_to_execute = None
    command_index = None

    # Try to parse as index first
    try:
        index = int(command_identifier) - 1  # Convert to 0-based index
        if 0 <= index < len(ALL_COMMANDS):
            command_to_execute = ALL_COMMANDS[index]
            command_index = index + 1
    except ValueError:
        # Not a number, try to find by name
        for i, command in enumerate(ALL_COMMANDS):
            if command.name().lower() == command_identifier.lower():
                command_to_execute = command
                command_index = i + 1
                break

    if command_to_execute is None:
        print(f"❌ Command '{command_identifier}' not found.")
        print("Use --list to see available commands.")
        sys.exit(1)

    print("=" * 60)
    print("🔧 EXECUTING SPECIFIC COMMAND")
    print("=" * 60)
    print(f"🚀 Executing command {command_index}: {command_to_execute.name()}")
    print(f"📝 Description: {command_to_execute.description()}")
    print(f"⏳ Executing...")
    
    try:
        success = command_to_execute.execute(env)
        if success:
            print(f"✅ Command completed successfully!")
        else:
            print(f"❌ Command failed!")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Command failed with exception!")
        print(f"💥 Error: {str(e)}")
        sys.exit(1)
    
    print("🎉 Command execution completed!")
    print("=" * 60)

def main():
    """
    Parse command line arguments and execute appropriate action.
    """
    parser = argparse.ArgumentParser(
        description="Configure your system for server use with QEMU/KVM virtualization.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  configure.py                     # Run all configuration commands
  configure.py --list-workflows    # List all available workflows
  configure.py --list-commands     # List all available commands
  configure.py --workflow <index or name>   # Execute a specific workflow
  configure.py --yaml-workflow <file.yaml>  # Execute workflow from YAML file
  configure.py --command <index or name>    # Execute command
        """
    )

    parser.add_argument(
        "--list-workflows", 
        action="store_true",
        help="List all available configuration workflows"
    )

    parser.add_argument(
        "--list-commands", 
        action="store_true",
        help="List all available configuration commands"
    )
    
    parser.add_argument(
        "--workflow", 
        metavar="ID_OR_NAME",
        help="Execute only the specified workflow (by number or name)"
    )

    parser.add_argument(
        "--yaml-workflow", 
        metavar="FILE.yaml",
        help="Execute workflow defined in a YAML file"
    )

    parser.add_argument(
        "--command", 
        metavar="ID_OR_NAME",
        help="Execute only the specified command (by number or name)"
    )
    
    args = parser.parse_args()
    
    path = os.path.dirname(os.path.abspath(__file__)) + '/workflows'
    print("Loading workflows from:", path)
    load_workflows(path)

    if args.list_workflows:
        list_workflows()
    elif args.list_commands:
        list_commands()
    elif args.command:
        execute_specific_command(args.command)
    elif args.yaml_workflow:
        execute_yaml_workflow(args.yaml_workflow)
    elif args.workflow:
        execute_workflow(args.workflow)
    else:
        list_workflows()
        workflow_choice = numbered_prompt("Select workflow for this node", 1, len(WORKFLOWS))
        if workflow_choice is not None:
            execute_workflow(workflow_choice)

if __name__ == "__main__":
    main()
