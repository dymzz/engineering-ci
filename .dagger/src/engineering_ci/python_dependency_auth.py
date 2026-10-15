"""Private Git installation is confined to dependency synchronization."""

TOKEN_VARIABLE = "ENGINEERING_CI_GIT_TOKEN"
# Git supplies the operation as $1. The helper stores only an environment lookup,
# never the credential, and is scoped to GitHub HTTPS remotes.
CREDENTIAL_HELPER = (
    '!f() { if [ "$1" = get ]; then '
    "printf 'username=x-access-token\\npassword=%s\\n' "
    '"$ENGINEERING_CI_GIT_TOKEN"; fi; }; f'
)


def prepare_private_git(container):
    return (
        container.with_exec(["apt-get", "update", "-qq"])
        .with_exec(
            [
                "apt-get",
                "install",
                "-y",
                "-qq",
                "--no-install-recommends",
                "git",
                "ca-certificates",
            ]
        )
        .with_exec(
            [
                "git",
                "config",
                "--global",
                "credential.https://github.com.helper",
                CREDENTIAL_HELPER,
            ]
        )
        .with_env_variable("GIT_TERMINAL_PROMPT", "0")
    )


def sync_dependencies(container, python_version, git_token=None):
    if git_token is not None:
        container = container.with_secret_variable(TOKEN_VARIABLE, git_token)
    container = container.with_exec(
        ["python", "/tmp/engineering-ci-uv-sync.py", python_version]
    )
    if git_token is not None:
        container = container.without_secret_variable(TOKEN_VARIABLE).with_exec(
            [
                "python",
                "-c",
                f"import os; assert {TOKEN_VARIABLE!r} not in os.environ",
            ]
        )
    return container
