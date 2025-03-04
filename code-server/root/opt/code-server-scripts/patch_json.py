#!/usr/bin/env python3

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

# ------------------------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------------------------


def setup_logger(log_level: int = logging.INFO) -> logging.Logger:
    """
    Creates and returns a configured logger.
    """
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)

    # Prevent duplicate handlers if function called multiple times
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def read_json(file_path: str, logger: logging.Logger) -> Dict[str, Any]:
    """
    Read and return JSON from the given file path. If the file does not exist,
    returns an empty dictionary.
    """
    if not os.path.exists(file_path):
        logger.info("File '%s' does not exist. Returning empty dictionary.", file_path)
        return {}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            logger.debug("Successfully read JSON from '%s'.", file_path)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read JSON from '%s': %s", file_path, e)
        return {}


def write_json(
    file_path: str, data: Dict[str, Any], logger: logging.Logger, sort_keys: bool = True
) -> None:
    """
    Write the given dictionary to a JSON file at the specified path, creating
    directories as needed.
    """
    try:
        logger.debug("Ensuring directory for '%s' exists.", file_path)
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        logger.debug("Writing updated JSON data to '%s'.", file_path)
        with open(file_path, "w", encoding="utf-8") as f_out:
            json.dump(data, f_out, ensure_ascii=False, indent=2, sort_keys=sort_keys)
        logger.info("Successfully wrote JSON to '%s'.", file_path)
    except OSError as e:
        logger.error("Failed to write JSON to '%s': %s", file_path, e)


# ------------------------------------------------------------------------------
# Patch Functions
# ------------------------------------------------------------------------------


def apply_force_patch(
    original: Dict[str, Any],
    patch_data: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """
    Recursively overwrite all matching keys in 'original' with 'patch_data'.
    """
    if logger is None:
        logger = setup_logger()

    if not isinstance(original, dict) or not isinstance(patch_data, dict):
        logger.debug(
            "Either 'original' or 'patch_data' is not a dictionary. Returning 'patch_data'."
        )
        return patch_data

    for key, value in patch_data.items():
        if (
            isinstance(value, dict)
            and key in original
            and isinstance(original[key], dict)
        ):
            logger.debug("Recursively forcing patch for key '%s'.", key)
            original[key] = apply_force_patch(original[key], value, logger)
        else:
            logger.debug("Overwriting key '%s' with new value.", key)
            original[key] = value
    return original


def apply_keep_patch(
    original: Dict[str, Any],
    patch_data: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """
    Only add keys from 'patch_data' if they do not exist in 'original'.
    """
    if logger is None:
        logger = setup_logger()

    if not isinstance(original, dict) or not isinstance(patch_data, dict):
        logger.debug(
            "Either 'original' or 'patch_data' is not a dictionary. Returning 'original'."
        )
        return original

    for key, value in patch_data.items():
        if key not in original:
            logger.debug("Key '%s' not in original. Adding it.", key)
            original[key] = value
        else:
            if isinstance(value, dict) and isinstance(original[key], dict):
                logger.debug(
                    "Recursively keeping existing keys and adding missing for '%s'.",
                    key,
                )
                apply_keep_patch(original[key], value, logger)
            else:
                logger.debug("Keeping existing value for key '%s'. No changes.", key)
    return original


# ------------------------------------------------------------------------------
# Main Patch Operation
# ------------------------------------------------------------------------------


def patch_json(
    source_file_path: str,
    patch_files: list,
    output_file_path: Optional[str] = None,
    force: bool = False,
    logger: Optional[logging.Logger] = None,
    sort_keys: bool = True,
) -> None:
    """
    Reads in a source JSON file, applies patches from multiple JSON files,
    and writes out the result to a given output file path. If 'force' is True,
    keys in the source are overwritten. Otherwise, only missing keys are added.
    """
    if logger is None:
        logger = setup_logger()

    logger.info(
        "Starting patch operation. source='%s', patches='%s', output='%s', force=%s",
        source_file_path,
        patch_files,
        output_file_path,
        force,
    )

    source_data = read_json(source_file_path, logger)
    for patch_file in patch_files:
        if not os.path.exists(patch_file):
            logger.warning(
                "Patch file '%s' does not exist. Skipping patch operation.", patch_file
            )
            continue
        patch_data = read_json(patch_file, logger)
        if force:
            source_data = apply_force_patch(source_data, patch_data, logger)
        else:
            source_data = apply_keep_patch(source_data, patch_data, logger)

    if output_file_path:
        write_json(output_file_path, source_data, logger, sort_keys=sort_keys)
    else:
        json.dump(
            source_data, sys.stdout, ensure_ascii=False, indent=2, sort_keys=sort_keys
        )
        sys.stdout.write("\n")
    logger.info("Patch operation with multiple files completed successfully.")


# ------------------------------------------------------------------------------
# Command-Line Interface
# ------------------------------------------------------------------------------

# Log levels mapping
VALID_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Patch JSON files.\n\n"
            "Single patch example:\n"
            "  python patch_json.py --source source.json patch.json\n\n"
            "Multiple patches example:\n"
            "  python patch_json.py --source source.json patch1.json patch2.json\n\n"
            "In-place example:\n"
            "  python patch_json.py --source source.json --in-place patch.json\n"
        )
    )
    parser.add_argument("--source", required=True, help="Path to the source JSON file.")
    parser.add_argument(
        "--output",
        help="Path where the output JSON file will be saved. If not set, prints to stdout.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Apply patches directly to the source file.",
    )
    parser.add_argument(
        "patches", nargs="*", help="One or more patch JSON files to apply in order."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Whether to force overwrite existing keys (default: only add missing keys).",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Default: INFO",
    )
    parser.add_argument(
        "--no-sort-keys",
        action="store_false",
        dest="sort_keys",
        default=True,
        help="Disable sorting of JSON keys. Sorting is enabled by default.",
    )
    args = parser.parse_args()

    if args.in_place:
        args.output = args.source

    # Validate and map string log level to numeric value
    user_level = args.log_level.upper()
    if user_level not in VALID_LOG_LEVELS:
        print(f"Invalid log level: '{user_level}'. Defaulting to 'INFO'.")
        numeric_log_level = logging.INFO
    else:
        numeric_log_level = VALID_LOG_LEVELS[user_level]

    logger = setup_logger(numeric_log_level)

    patch_json(
        args.source,
        args.patches,
        args.output,
        args.force,
        logger,
        sort_keys=args.sort_keys,
    )


if __name__ == "__main__":
    main()
