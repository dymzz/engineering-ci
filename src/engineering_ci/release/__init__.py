"""Manual release policy and immutable evidence; GitHub Actions owns the DAG."""

RELEASE_CONFIG = {"workflow": "release.yml", "branch": "main"}
PR_CI_JOBS = ("python-ci / checks", "sync-tool-tests", "version-metadata")
