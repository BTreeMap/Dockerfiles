#!/usr/bin/env python3

import argparse


def modify_resolv_conf(
    nameserver: str = "100.100.100.100", resolv_conf_path: str = "/etc/resolv.conf"
) -> None:
    new_nameserver = f"nameserver {nameserver}\n"

    try:
        with open(resolv_conf_path, "r") as file:
            lines = file.readlines()

        # Remove existing nameserver lines
        lines = [line for line in lines if not line.startswith("nameserver")]

        # Insert the new nameserver line at the beginning
        lines.insert(0, new_nameserver)

        # Write the modified lines back to the resolv.conf file
        with open(resolv_conf_path, "w") as file:
            file.writelines(lines)

        print(f"Successfully updated {resolv_conf_path} with {new_nameserver.strip()}")
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
