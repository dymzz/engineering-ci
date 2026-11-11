"""Pinned Weaver CLI release asset for Selection closures and Dagger download.

Single authority for version, asset URL and SHA-256. Selection records this
identity in post-completion closures; the Dagger module downloads and verifies
the same asset bytes. Do not duplicate these pins elsewhere.
"""

VERSION = "0.28.0"
LINUX_SHA256 = "9878e2d56ad82ae441b0b527377a4f6001ae19f4af362fb0a4d951884f59ab61"
LINUX_ASSET = (
    "https://github.com/open-telemetry/weaver/releases/download/"
    f"v{VERSION}/weaver-x86_64-unknown-linux-gnu.tar.xz"
)

# Closure identity recorded in post-completion plans.
RECORD = {
    "version": VERSION,
    "sha256": LINUX_SHA256,
    "asset": LINUX_ASSET,
}
