#!/usr/bin/env sh

# Define the virtual environment directory, defaulting to /opt/shared-venv if not set
VENV_DIR="${VENV_DIR:-/opt/shared-venv}"

# Check if the virtual environment directory exists and contains a Python executable
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python" ]; then
    # Activate the virtual environment
    export PATH="$VENV_DIR/bin:$PATH"
    export VIRTUAL_ENV="$VENV_DIR"
else
    # Fallback message if the virtual environment is not available
    echo "Warning: Virtual environment not found or not executable in $VENV_DIR"
fi
