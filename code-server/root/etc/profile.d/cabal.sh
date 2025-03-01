#!/bin/sh

if [ "$(id -u)" = "0" ]; then
  exit 0
fi

CABAL_DIR=~/.haskell

export CABAL_DIR
export PATH="$CABAL_DIR/bin:$PATH"

if [ ! -d "$CABAL_DIR/packages/hackage.haskell.org" ]; then
  cabal update
fi
