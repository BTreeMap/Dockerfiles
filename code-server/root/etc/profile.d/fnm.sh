#!/usr/bin/env sh

FNM_DIR="${FNM_DIR:-/opt/fnm}"

# Check if the FNM_DIR is set and contains an executable fnm binary
if [ -d "$FNM_DIR" ] && [ -x "$FNM_DIR/fnm" ]; then
    # Initialize fnm environment
    eval "$($FNM_DIR/fnm env)"
else
    # Fallback message if fnm is not found
    echo "Warning: fnm is not installed or not executable in $FNM_DIR"
fi
