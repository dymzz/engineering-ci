"""Pinned official OpenTelemetry SemConv Registry identity for CI/CD checks.

Single authority for the upstream Registry bytes consumed by the CI/CD Weaver
fixture. The pin is the authority published *outside* this repository, so a
coordinated edit of the local Registry and its manifest cannot reproduce it:
``TREE_SHA256`` was computed offline from the upstream ``model/`` tree at
``COMMIT``, and the local fixture reproduces that tree object exactly
(``GIT_TREE_SHA1``).

Do not add local requirements to the pinned Registry. Upstream SemConv
``v1.45.0`` declares no entity association for ``span.cicd.pipeline.run.server``,
so it defines no Resource Required obligation for the Pipeline span. ECI-owned
requirements are separate policies with their own rule IDs and evidence.
"""

from __future__ import annotations

import hashlib
import json

REPOSITORY = "https://github.com/open-telemetry/semantic-conventions"
COMMIT = "fb30ca17193b551473b1f43c315482918ab6987c"
VERSION = "1.45.0"

# Canonical SHA-256 over the upstream model/ Registry-relative path -> file
# SHA-256 map. Recomputable with ``registry_tree_digest``.
TREE_SHA256 = "85e58bf616303da0488f2cf86f0bb8f9e91ae5fd45e5b38399fa97a288e10963"

# Upstream git tree object of model/ at COMMIT, published by the upstream
# repository. The local fixture directory hashes to this same tree object.
GIT_TREE_SHA1 = "b9a819cdbafe936394bd596bb27c3d929ba24ff4"

# SHA-256 of the upstream model/cicd/spans.yaml blob at COMMIT.
CICD_SPANS_SHA256 = "10e19a1811af95b033fb8a9e642865f55a1b66718d1935cfb8d6df060e2d7f86"


def registry_tree_digest(files: dict[str, str]) -> str:
    """Canonical digest over a Registry-relative path -> file SHA-256 map.

    The container runtime recomputes this over the Registry it actually read,
    so both sides must agree on the canonicalisation.
    """
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


RECORD = {
    "repository": REPOSITORY,
    "commit": COMMIT,
    "version": VERSION,
    "tree_sha256": TREE_SHA256,
    "git_tree_sha1": GIT_TREE_SHA1,
}
