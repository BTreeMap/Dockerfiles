#!/usr/bin/env python3

import argparse


def modify_resolv_conf(
    nameserver: str = "100.100.100.100", resolv_conf_path: str = "/etc/resolv.conf"
) -> None:
    """
    Modify the /etc/resolv.conf file to update the nameserver and remove existing nameserver and comment lines.
    This script is used to ensure that Tailscale Magic DNS is correctly enabled in the Docker environment.
    Args:
        nameserver (str): The new nameserver IP address to be added. Defaults to "100.100.100.100".
        resolv_conf_path (str): The path to the resolv.conf file. Defaults to "/etc/resolv.conf".
    Returns:
        None
    Raises:
        Exception: If there is an error reading or writing the resolv.conf file.
    """
    try:
        with open(resolv_conf_path, "r") as file:
            lines = file.readlines()

        # Remove existing nameserver lines
        lines = [line for line in lines if not line.startswith("nameserver")]
        # Remove existing comment lines
        lines = [line for line in lines if not line.startswith("#")]

        # Create a list with the new comments and nameserver
        new_lines = [
            "# This file was modified by modify_resolv_conf.py\n",
            "# Reason: Ensure Tailscale Magic DNS is correctly enabled in the Docker environment\n",
            "\n",
            f"nameserver {nameserver}\n",
        ]

        # Extend the new_lines list with the existing lines
        new_lines.extend(lines)

        # Write the modified lines back to the resolv.conf file
        with open(resolv_conf_path, "w") as file:
            file.writelines(new_lines)

        print(f"Successfully updated {resolv_conf_path} with {nameserver}")
    except Exception as e:
        print(f"Failed to update {resolv_conf_path}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Modify /etc/resolv.conf to set a specific nameserver."
    )
    parser.add_argument(
        "--nameserver",
        type=str,
        default="100.100.100.100",
        help="The nameserver to set in /etc/resolv.conf",
        required=False,
    )
    args = parser.parse_args()

    modify_resolv_conf(nameserver=args.nameserver)
