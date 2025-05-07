#!/usr/bin/env sh

# Check if the FNM_DIR is set and contains an executable fnm binary
if [ -n "$FNM_DIR" ] && [ -x "$FNM_DIR/fnm" ]; then
    # Initialize fnm environment
    eval "$($FNM_DIR/fnm env)"
fi
