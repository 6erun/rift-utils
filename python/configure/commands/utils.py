
import subprocess

CLOUDRIFT_MEDIA_MOUNT = '/media/cloudrift'

DRY_RUN = False

def set_dry_run(value: bool) -> None:
    global DRY_RUN
    DRY_RUN = value

def is_dry_run() -> bool:
    return DRY_RUN

def run(cmd, check=True, capture_output=False, quiet_stderr=False, shell=False):
    cmd_str = ' '.join(cmd) if isinstance(cmd, list) else cmd
    if DRY_RUN:
        print(f"[DRY RUN] Would run: {cmd_str}")
        return "", None, 0
    kwargs = {}
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["text"] = True
    if quiet_stderr:
        kwargs["stderr"] = subprocess.DEVNULL
    print(f"Running command: {cmd_str}")
    result = subprocess.run(cmd, check=check, shell=shell, **kwargs)
    stdout = result.stdout.strip() if capture_output and result.stdout else ""
    return stdout, result.stderr if quiet_stderr else None, result.returncode

def write_file(path, content: str) -> None:
    """Write content to a file, or print what would be written in dry-run mode."""
    path_str = str(path)
    if DRY_RUN:
        print(f"[DRY RUN] Would write to {path_str}:")
        for line in content.splitlines():
            print(f"  {line}")
        return
    with open(path_str, 'w') as f:
        f.write(content)

def yes_no_prompt(prompt: str, default: bool) -> bool:
    if DRY_RUN:
        print(f"[DRY RUN] {prompt} -> {'yes' if default else 'no'} (using default)")
        return default
    default_input = 'y' if default else 'n'
    default_yes = 'Y' if default else 'y'
    default_no = 'N' if not default else 'n'
    print(f"{prompt} ({default_yes}/{default_no})")
    return (input() or default_input).lower() == 'y'

def numbered_prompt(prompt: str, min_index: int, max_index: int) -> int | None:
    if DRY_RUN:
        print(f"[DRY RUN] {prompt} -> {min_index} (using first option)")
        return min_index
    print(prompt)
    while True:
        try:
            value = input(f"Enter a number ({min_index}-{max_index}) or <Enter> for exit: ")
            if value == "":
                return None
            choice = int(value)
            if min_index <= choice <= max_index:
                return choice
            else:
                print(f"Please enter a number between {min_index} and {max_index}.")
        except ValueError:
            print("Invalid input. Please enter a valid number.")

def reboot_prompt():
    if yes_no_prompt("\nReboot now?", False):
        print("Rebooting...")
        run(["reboot"])
    else:
        print("Please reboot at your convenience to apply the changes.")

def apt_install(packages):
    print(f"Updating apt and installing packages {packages}...")
    run(["apt", "update"])
    run(["apt", "install", "-y", *packages])

def add_mp_to_fstab(fstab_line, mount_point) -> bool:
    """
    Adds the mount point to /etc/fstab to persist across reboots.
    """
    try:
        # Check if the line already exists
        with open("/etc/fstab", 'r') as f:
            if fstab_line in f.read():
                print(f"Mount point '{mount_point}' already exists in /etc/fstab.")
                return True
    except FileNotFoundError:
        print("Error: /etc/fstab not found.")
        return False

    # Use tee to append the line
    try:
        command = f'echo "{fstab_line}" | tee -a /etc/fstab'
        run(command, check=True, shell=True)
        print(f"Successfully added '{fstab_line.strip()}' to /etc/fstab.")
    except subprocess.CalledProcessError as e:
        print(f"Error adding mount to /etc/fstab. Return code: {e.returncode}")
        return False
    
    return True