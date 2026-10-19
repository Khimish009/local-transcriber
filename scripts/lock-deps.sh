#!/usr/bin/env sh
# Regenerate backend/requirements*.lock from backend/requirements*.txt (TASKS.md T13.3).
#
#   ./scripts/lock-deps.sh             # after editing a requirements*.txt; other pins stay put
#   ./scripts/lock-deps.sh --upgrade   # move everything to the newest allowed versions
#
# The images install only what the locks list, with hashes, so a clean build gets exactly
# the versions that were tested. After an upgrade, run `python -m benchmark.equivalence`
# and a real recording before committing: diarization numerics depend on numpy and scipy.
#
# Needs uv (https://docs.astral.sh/uv/) and git — gigaam is resolved from its repository.
# The locks are universal: one file serves linux/amd64 and linux/arm64.
set -eu
cd "$(dirname "$0")/../backend"

command -v uv >/dev/null 2>&1 || {
  echo "uv is not installed: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
}

compile() {
  uv pip compile --quiet --universal --python-version 3.11 --generate-hashes \
    --custom-compile-command "./scripts/lock-deps.sh" "$@"
}
# The +cpu torch wheels exist only on the PyTorch index; everything else comes from PyPI.
# Mixing indexes is safe here because every file is pinned by its hash.
torch_index="--index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cpu"

# Shared API dependencies first: the worker lock is constrained to the same versions, so
# both images run the same fastapi, redis and rq.
compile requirements.txt -o requirements.lock "$@"
# shellcheck disable=SC2086
compile requirements.txt requirements-torch.txt requirements-worker.txt \
  -c requirements.lock $torch_index --no-emit-package gigaam \
  -o requirements-worker.lock "$@"
# The torch layer of the worker image: the same versions, as a subset.
# shellcheck disable=SC2086
compile requirements-torch.txt -c requirements-worker.lock $torch_index \
  -o requirements-torch.lock "$@"
