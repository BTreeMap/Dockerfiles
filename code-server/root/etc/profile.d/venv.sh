#!/usr/bin/env sh

# Define the virtual environment directory, defaulting to /opt/shared-venv if not set
VENV_DIR="${VENV_DIR:-/opt/shared-venv}"

# Check if the virtual environment directory exists and contains an activate script
if [ -f "$VENV_DIR/bin/activate" ]; then
    # Activate the virtual environment
    source "$VENV_DIR/bin/activate"
else
    # Fallback message if the virtual environment is not available
    echo "Warning: Virtual environment not found or not executable in $VENV_DIR"
fi
