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

# Bridge execution options
export BRIDGE_TYPE=${BRIDGE_TYPE:-}
export BBCTL_NO_OVERRIDE_CONFIG=${BBCTL_NO_OVERRIDE_CONFIG:-}
export BRIDGE_EXTRA_ARGS=${BRIDGE_EXTRA_ARGS:-}

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

# Construct bridge run command with options
RUN_ARGS=""

# Add no-override-config flag if set
if [[ -n "${BBCTL_NO_OVERRIDE_CONFIG}" ]]; then
    RUN_ARGS="${RUN_ARGS} --no-override-config"
fi

# Add bridge type if specified
if [[ -n "${BRIDGE_TYPE}" ]]; then
    RUN_ARGS="${RUN_ARGS} --type ${BRIDGE_TYPE}"
fi

# Add any extra arguments
if [[ -n "${BRIDGE_EXTRA_ARGS}" ]]; then
    RUN_ARGS="${RUN_ARGS} ${BRIDGE_EXTRA_ARGS}"
fi

# Run the specified bridge
bbctl -e $BEEPER_ENV run ${RUN_ARGS} $BRIDGE_NAME
