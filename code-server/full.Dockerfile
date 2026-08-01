# =============================================================================
# Full image, published as :code-server-full
#
# :code-server plus Racket -- i.e. the setup :code-server itself carried until
# Racket was trimmed out of it for the storage. Racket went unused for long
# enough to stop paying for its size in the image everything pulls, but not
# long enough to delete, so it lives here instead of nowhere.
#
# A layer over the published :code-server rather than a second copy of its
# ~250 lines: the two would drift the first time either was touched, and every
# byte that differs between them is in this file. The tag floats, exactly as
# the toolchain tags ./Dockerfile reads do -- a build picks up whatever the
# last completed run published, at most 12 hours old on the current schedule.
# =============================================================================
FROM ghcr.io/btreemap/dockerfiles:code-server

# Restore Racket to the front of the PATH ./Dockerfile builds.
ENV PATH=$RACKET_HOME/bin:$PATH

# The Racket editor settings, which patch-json applies by globbing this
# directory -- so they ship here rather than in ./root, where they would
# configure magic-racket against an interpreter :code-server no longer has.
COPY root-full/ /

# Racket, for development and execution of Racket programs. Built by
# ./racket.Dockerfile; the --chown is what ./Dockerfile's toolchain copies do,
# for the same reason -- the artifact image carries root-owned files.
COPY --from=ghcr.io/btreemap/dockerfiles:code-server-racket \
    --chown=${PUID}:${PGID} $RACKET_HOME $RACKET_HOME
