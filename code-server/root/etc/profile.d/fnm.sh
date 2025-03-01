#!/bin/sh

if [ -n "$FNM_DIR" ] && [ -x "$FNM_DIR/fnm" ]; then
	eval "$($FNM_DIR/fnm env)"
fi
