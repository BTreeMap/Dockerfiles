#!/bin/sh

CABAL_DIR=/config/.haskell

# Create CABAL_DIR if it doesn't exist and set permissions to 777
if [ ! -d "$CABAL_DIR" ]; then
  mkdir -p "$CABAL_DIR"
fi

chmod 777 "$CABAL_DIR"

export CABAL_DIR
export PATH="$CABAL_DIR/bin:$PATH"

if [ ! -d "$CABAL_DIR/packages/hackage.haskell.org" ]; then
  cabal update
fi
