#!/usr/bin/env python3
"""
Fetches active CloudRift instances and displays their SSH instructions.
"""

import argparse
import base64
import os
import platform
import sys
from pathlib import Path

import requests
import yaml


def get_credentials_path() -> Path:
    """Get the credentials file path based on the OS."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "rs.CloudRift" / "credentials.yml"
    else:  # Linux and others
        return Path.home() / ".config" / "cloudrift" / "credentials.yml"


def load_credentials() -> dict:
    """Load credentials from the YAML file."""
    creds_path = get_credentials_path()
    if not creds_path.exists():
        print(f"Error: Credentials file not found at {creds_path}", file=sys.stderr)
        sys.exit(1)

    with open(creds_path) as f:
        creds = yaml.safe_load(f)

    if not creds or "email" not in creds or "password" not in creds:
        print("Error: Credentials file must contain 'email' and 'password' fields", file=sys.stderr)
        sys.exit(1)

    return creds


def login(email: str, password: str, base_url: str = "https://api.cloudrift.ai") -> str:
    """Login and return the JWT token."""
    resp = requests.post(
        f"{base_url}/api/v1/auth/login",
        headers={
            "Content-Type": "application/json",
            "Referer": "https://console.cloudrift.ai/",
        },
        json={
            "version": "~upcoming",
            "data": {
                "email": email,
                "password": password,
            },
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data["data"]["token"]


def list_instances(token: str, base_url: str = "https://api.cloudrift.ai") -> list:
    """List active instances."""
    resp = requests.post(
        f"{base_url}/api/v1/instances/list",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Referer": "https://console.cloudrift.ai/",
        },
        json={
            "version": "~upcoming",
            "data": {
                "selector": {
                    "ByStatus": ["Initializing", "Active", "Deactivating"],
                },
            },
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]["instances"]


def render_instructions(instructions: dict) -> str:
    """Decode and render the instructions template with placeholder values."""
    template_b64 = instructions.get("instructions_template", "")
    if not template_b64:
        return ""

    template = base64.b64decode(template_b64).decode("utf-8")

    placeholders = instructions.get("placeholder_values", [])
    for key, value in placeholders:
        template = template.replace(f"{{{key}}}", value)

    return template


def parse_args() -> argparse.Namespace:
    default_url = os.environ.get("CLOUDRIFT_API_URL", "https://api.cloudrift.ai")
    parser = argparse.ArgumentParser(
        description="Fetch active CloudRift instances and display their SSH instructions."
    )
    parser.add_argument(
        "--base-url",
        default=default_url,
        help=f"API base URL (default: $CLOUDRIFT_API_URL or https://api.cloudrift.ai)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    base_url = args.base_url

    creds = load_credentials()
    print(f"Logging in as {creds['email']} to {base_url}...", file=sys.stderr)

    token = login(creds["email"], creds["password"], base_url)
    print("Login successful. Fetching instances...", file=sys.stderr)

    instances = list_instances(token, base_url)
    print(f"Found {len(instances)} instance(s).\n", file=sys.stderr)

    for i, instance in enumerate(instances):
        instructions = instance.get("instructions")
        if instructions:
            print(f"=== Instance {i + 1} ===")
            print(render_instructions(instructions))
            print()
        else:
            print(f"=== Instance {i + 1} ===")
            print("No instructions available.\n")


if __name__ == "__main__":
    main()
