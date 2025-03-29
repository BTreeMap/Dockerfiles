#!/bin/bash
#
# Beeper Bridge Runner Script
# This script configures and executes a Beeper bridge service
# using the provided configurations or environment variables.
#

# Exit on error, undefined variable, or pipe failure
set -euf -o pipefail

#
# CONFIGURATION SECTION
#

# Determine the bridge name from environment variable or command line argument
if [[ -z "${BRIDGE_NAME:-}" ]]; then
	if [[ ! -z "$1" ]]; then
		export BRIDGE_NAME="$1"
	else
		echo "BRIDGE_NAME not set"
		exit 1
	fi
fi

# Set default configuration paths
export BBCTL_CONFIG=${BBCTL_CONFIG:-/tmp/bbctl.json}
export BEEPER_ENV=${BEEPER_ENV:-prod}

#
# SETUP SECTION
#

# Create configuration file if it doesn't exist
if [[ ! -f $BBCTL_CONFIG ]]; then
	# Verify required authentication token
	if [[ -z "${MATRIX_ACCESS_TOKEN:-}" ]]; then
		echo "MATRIX_ACCESS_TOKEN not set"
		exit 1
	fi
	
	# Ensure data directory exists
	export DATA_DIR=${DATA_DIR:-/data}
	if [[ ! -d $DATA_DIR ]]; then
		echo "DATA_DIR ($DATA_DIR) does not exist, creating"
		mkdir -p $DATA_DIR
	fi
	
	# Create database directory
	export DB_DIR=${DB_DIR:-/data/db}
	mkdir -p $DB_DIR
	
	# Generate configuration JSON file
	jq -n '{environments: {"\(env.BEEPER_ENV)": {access_token: env.MATRIX_ACCESS_TOKEN, database_dir: env.DB_DIR, bridge_data_dir: env.DATA_DIR}}}' > $BBCTL_CONFIG
fi

#
# EXECUTION SECTION
#

# Run the specified bridge
bbctl -e $BEEPER_ENV run $BRIDGE_NAME
