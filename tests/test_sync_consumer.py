"""Unit tests for consumer SHA synchronization (no network, no Dagger required)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from engineering_ci.tools import consumer as sync

UPSTREAM = "dymzz/engineering-ci"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
WORKFLOW = (
    "jobs:\n  python:\n    uses: "
    "dymzz/engineering-ci/.github/workflows/reusable-python-ci.yml@"
    + OLD_SHA
    + "\n    secrets:\n      ENGINEERING_CI_READ_TOKEN: ${{ secrets.ENGINEERING_CI_READ_TOKEN }}\n"
)
MANIFEST = json.dumps(
    {
        "name": "smoke",
        "dependencies": [
            {
                "name": "engineering-ci",
                "source": "github.com/dymzz/engineering-ci@" + OLD_SHA,
                "pin": OLD_SHA,
            },
            {
                "name": "other",
                "source": "github.com/example/other@" + OLD_SHA,
                "pin": OLD_SHA,
            },
        ],
    }
)


class VersionSyncTests(unittest.TestCase):
    def test_both_refs_and_other_dependencies_preserved(self):
        wf, man, notes = sync.update_references(WORKFLOW, MANIFEST, UPSTREAM, NEW_SHA)
        self.assertIn("reusable-python-ci.yml@" + NEW_SHA, wf)
        self.assertIn("${{ secrets.ENGINEERING_CI_READ_TOKEN }}", wf)
        deps = json.loads(man)["dependencies"]
        self.assertEqual(
            deps[0]["source"], "github.com/dymzz/engineering-ci@" + NEW_SHA
        )
        self.assertEqual(deps[0]["pin"], NEW_SHA)
        self.assertEqual(deps[1]["pin"], OLD_SHA)
        self.assertEqual(len(notes), 2)

    def test_idempotent(self):
        wf, man, _ = sync.update_references(WORKFLOW, MANIFEST, UPSTREAM, OLD_SHA)
        self.assertEqual(wf, WORKFLOW)
        self.assertEqual(json.loads(man), json.loads(MANIFEST))

    def test_wrong_source_rejected(self):
        bad = MANIFEST.replace(
            "github.com/dymzz/engineering-ci@", "github.com/other/engineering-ci@"
        )
        with self.assertRaises(sync.SyncError):
            sync.update_references(WORKFLOW, bad, UPSTREAM, NEW_SHA)

    def test_missing_and_ambiguous_workflow_rejected(self):
        for value in ["name: empty\n", WORKFLOW + WORKFLOW]:
            with self.subTest(value=value), self.assertRaises(sync.SyncError):
                sync.update_references(value, MANIFEST, UPSTREAM, NEW_SHA)

    def test_missing_dependency_rejected(self):
        bad = MANIFEST.replace('"name": "engineering-ci"', '"name": "missing"')
        with self.assertRaises(sync.SyncError):
            sync.update_references(WORKFLOW, bad, UPSTREAM, NEW_SHA)

    def test_invalid_sha_rejected(self):
        for value in ["main", "1234", "z" * 40, "A" * 40]:
            with self.subTest(value=value), self.assertRaises(sync.SyncError):
                sync.update_references(WORKFLOW, MANIFEST, UPSTREAM, value)

    def test_git_root_normalizes_path_and_resolves(self):
        original = sync.git
        root = Path(__file__).resolve().parent.parent
        try:
            sync.git = lambda repo, *args: root.as_posix()
            self.assertEqual(sync.git_root(root), root)
        finally:
            sync.git = original

    def test_missing_remote_branch_rejected(self):
        original = sync.git
        try:
            sync.git = lambda repo, *args: ""
            with self.assertRaises(sync.SyncError):
                sync.remote_branch_sha(Path("/tmp"), "main")
        finally:
            sync.git = original

    def test_perform_strips_inherited_eci_session_env_for_consumer_and_preserves_target_env(
        self,
    ):
        def clean_git_env(base=None):
            src = os.environ if base is None else base
            env = {
                k: v
                for k, v in src.items()
                if not k.upper().startswith("GIT_")
                and k.upper()
                not in {
                    "UV_PROJECT",
                    "UV_PROJECT_ENVIRONMENT",
                    "VIRTUAL_ENV",
                    "PYTHONPATH",
                }
            }
            env["GIT_AUTHOR_NAME"] = "Test Author"
            env["GIT_AUTHOR_EMAIL"] = "author@example.invalid"
            env["GIT_COMMITTER_NAME"] = "Test Committer"
            env["GIT_COMMITTER_EMAIL"] = "committer@example.invalid"
            return env

        def run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
            res = subprocess.run(
                ["git", *args],
                cwd=repo,
                env=clean_git_env() if env is None else env,
                check=True,
                capture_output=True,
                text=True,
            )
            return res.stdout.strip()

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            eci_remote = tmp / "eci-origin.git"
            run_git(tmp, "init", "--bare", "-q", str(eci_remote))
            eci_primary = tmp / "eci-primary"
            eci_primary.mkdir()
            run_git(eci_primary, "init", "-q", "-b", "primary-hold")
            (eci_primary / "README.md").write_text("eci\n", encoding="utf-8")
            run_git(eci_primary, "add", "README.md")
            run_git(eci_primary, "commit", "-qm", "eci init")
            run_git(eci_primary, "remote", "add", "origin", str(eci_remote))
            run_git(eci_primary, "push", "-q", "origin", "HEAD:refs/heads/main")
            eci_sha = run_git(eci_primary, "rev-parse", "HEAD")

            eci_venv = eci_primary / ".venv"
            (eci_venv / "Scripts").mkdir(parents=True)
            (eci_venv / "bin").mkdir(parents=True)
            eci_linked = tmp / "eci-linked"
            run_git(
                eci_primary,
                "worktree",
                "add",
                "-q",
                "-b",
                "main",
                str(eci_linked),
                "HEAD",
            )
            (eci_linked / "src" / "engineering_ci").mkdir(parents=True)

            cons_remote = tmp / "consumer-origin.git"
            run_git(tmp, "init", "--bare", "-q", str(cons_remote))
            consumer = tmp / "consumer"
            consumer.mkdir()
            run_git(consumer, "init", "-q", "-b", "main")
            (consumer / ".github" / "workflows").mkdir(parents=True)
            (consumer / ".github" / "workflows" / "ci.yml").write_text(
                WORKFLOW, encoding="utf-8"
            )
            (consumer / "dagger.json").write_text(MANIFEST, encoding="utf-8")
            (consumer / ".dagger").mkdir()
            (consumer / ".dagger" / "sdk.txt").write_text("old\n", encoding="utf-8")
            (consumer / "extra").mkdir()
            custom_venv = tmp / "custom-consumer-venv"
            custom_venv.mkdir()
            custom_lib = tmp / "custom-consumer-lib"
            custom_lib.mkdir()

            hook_log = tmp / "consumer-hook-env.jsonl"
            py_exe = Path(sys.executable).as_posix()
            hook_script = (
                "#!/bin/sh\n"
                f'"{py_exe}" -c \'import json, os, sys; '
                'payload = {"hook": sys.argv[1], "uv_project": os.environ.get("UV_PROJECT", ""), '
                '"uv_env": os.environ.get("UV_PROJECT_ENVIRONMENT", ""), '
                '"venv": os.environ.get("VIRTUAL_ENV", ""), '
                '"pypath": os.environ.get("PYTHONPATH", ""), '
                '"path": os.environ.get("PATH", "")}; '
                'open(sys.argv[2], "a", encoding="utf-8").write(json.dumps(payload) + "\\n")\' '
                '"__HOOK__" '
                f'"{hook_log.as_posix()}"\n'
            )
            hooks_dir = consumer / ".git" / "hooks"
            for name in ("pre-commit", "pre-push"):
                hfile = hooks_dir / name
                hfile.write_text(
                    hook_script.replace("__HOOK__", name), encoding="utf-8"
                )
                hfile.chmod(0o755)

            run_git(consumer, "add", "--all")
            run_git(consumer, "commit", "-qm", "consumer init")
            run_git(consumer, "remote", "add", "origin", str(cons_remote))
            run_git(consumer, "push", "-q", "-u", "origin", "main")
            hook_log.unlink(missing_ok=True)

            # 1. Inherited ECI session env + `uv run` activation is stripped for consumer.
            leaked_env = clean_git_env()
            leaked_env["UV_PROJECT"] = str(eci_linked)
            leaked_env["UV_PROJECT_ENVIRONMENT"] = str(eci_venv)
            leaked_env["VIRTUAL_ENV"] = str(eci_venv)
            leaked_env["PYTHONPATH"] = os.pathsep.join(
                [str(eci_linked / "src"), str(consumer / "extra")]
            )
            leaked_env["PATH"] = os.pathsep.join(
                [
                    str(eci_venv / "Scripts"),
                    str(eci_venv / "bin"),
                    leaked_env.get("PATH", ""),
                ]
            )

            recorded_dagger: list[tuple[list[str], dict[str, str]]] = []
            orig_run = sync.subprocess.run
            orig_env = os.environ.copy()

            def wrapped_run(cmd, *, cwd=None, env=None, **kwargs):
                if list(cmd[:1]) == ["dagger"]:
                    recorded_dagger.append((list(cmd), dict(env or {})))
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return orig_run(cmd, cwd=cwd, env=env, **kwargs)

            try:
                os.environ.clear()
                os.environ.update(leaked_env)
                sync.subprocess.run = wrapped_run
                args = Namespace(
                    consumer=consumer,
                    upstream=UPSTREAM,
                    branch="main",
                    apply=True,
                    commit=True,
                    push=True,
                )
                sync.perform(args, eci_linked)
            finally:
                sync.subprocess.run = orig_run
                os.environ.clear()
                os.environ.update(orig_env)

            self.assertEqual(
                [cmd for cmd, _ in recorded_dagger],
                [["dagger", "develop"], ["dagger", "check"]],
            )
            for _, denv in recorded_dagger:
                self.assertNotIn("UV_PROJECT", denv)
                self.assertNotIn("UV_PROJECT_ENVIRONMENT", denv)
                self.assertNotIn("VIRTUAL_ENV", denv)
                self.assertEqual(
                    Path(denv["PYTHONPATH"]).resolve(), (consumer / "extra").resolve()
                )
                path_parts = {
                    Path(p).resolve() for p in denv["PATH"].split(os.pathsep) if p
                }
                self.assertNotIn((eci_venv / "Scripts").resolve(), path_parts)
                self.assertNotIn((eci_venv / "bin").resolve(), path_parts)

            hook_records = [
                json.loads(line)
                for line in hook_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [r["hook"] for r in hook_records], ["pre-commit", "pre-push"]
            )
            for rec in hook_records:
                self.assertEqual(rec["uv_project"], "")
                self.assertEqual(rec["uv_env"], "")
                self.assertEqual(rec["venv"], "")
                self.assertEqual(
                    Path(rec["pypath"]).resolve(), (consumer / "extra").resolve()
                )
            self.assertIn(
                eci_sha, (consumer / "dagger.json").read_text(encoding="utf-8")
            )

            # 2. Intentional consumer target env (outside ECI and inside consumer) is preserved while ECI src/venv stay stripped.
            targeted_env = clean_git_env()
            targeted_env["UV_PROJECT"] = str(consumer)
            targeted_env["UV_PROJECT_ENVIRONMENT"] = str(custom_venv)
            targeted_env["VIRTUAL_ENV"] = str(eci_venv)
            targeted_env["PYTHONPATH"] = os.pathsep.join(
                [str(eci_linked / "src"), str(custom_lib)]
            )
            sanitized_target = sync.consumer_subprocess_env(
                eci_linked, consumer, base_env=targeted_env
            )
            self.assertEqual(
                Path(sanitized_target["UV_PROJECT"]).resolve(), consumer.resolve()
            )
            self.assertEqual(
                Path(sanitized_target["UV_PROJECT_ENVIRONMENT"]).resolve(),
                custom_venv.resolve(),
            )
            self.assertNotIn("VIRTUAL_ENV", sanitized_target)
            self.assertEqual(
                Path(sanitized_target["PYTHONPATH"]).resolve(), custom_lib.resolve()
            )

            # 3. Implicit `uv run` VIRTUAL_ENV matching active non-consumer sys.prefix (even out-of-repo)
            #    is stripped while preserving explicit external UV_PROJECT_ENVIRONMENT.
            out_of_repo_eci_venv = tmp / "out-of-repo-eci-venv"
            (out_of_repo_eci_venv / "Scripts").mkdir(parents=True)
            orig_prefix = sys.prefix
            orig_base_prefix = getattr(sys, "base_prefix", sys.prefix)
            try:
                sys.prefix = str(out_of_repo_eci_venv)
                sys.base_prefix = str(tmp / "system-python")
                implicit_env = clean_git_env()
                implicit_env["UV_PROJECT_ENVIRONMENT"] = str(custom_venv)
                implicit_env["VIRTUAL_ENV"] = str(out_of_repo_eci_venv)
                implicit_env["PATH"] = os.pathsep.join(
                    [
                        str(out_of_repo_eci_venv / "Scripts"),
                        implicit_env.get("PATH", ""),
                    ]
                )
                sanitized_implicit = sync.consumer_subprocess_env(
                    eci_linked, consumer, base_env=implicit_env
                )
            finally:
                sys.prefix = orig_prefix
                sys.base_prefix = orig_base_prefix
            self.assertEqual(
                Path(sanitized_implicit["UV_PROJECT_ENVIRONMENT"]).resolve(),
                custom_venv.resolve(),
            )
            self.assertNotIn("VIRTUAL_ENV", sanitized_implicit)
            implicit_path_parts = {
                Path(p).resolve()
                for p in sanitized_implicit["PATH"].split(os.pathsep)
                if p
            }
            self.assertNotIn(
                (out_of_repo_eci_venv / "Scripts").resolve(), implicit_path_parts
            )

            # 4. External shared env equal to the active interpreter prefix is the
            #    host session, not a consumer target.
            shared_host = tmp / "external-shared-host"
            (shared_host / "Scripts").mkdir(parents=True)
            try:
                sys.prefix = str(shared_host)
                sys.base_prefix = str(tmp / "system-python")
                host_env = clean_git_env()
                host_env["UV_PROJECT_ENVIRONMENT"] = str(shared_host)
                host_env["VIRTUAL_ENV"] = str(shared_host)
                host_env["PATH"] = os.pathsep.join(
                    [str(shared_host / "Scripts"), host_env.get("PATH", "")]
                )
                sanitized_host = sync.consumer_subprocess_env(
                    eci_linked, consumer, base_env=host_env
                )
            finally:
                sys.prefix = orig_prefix
                sys.base_prefix = orig_base_prefix
            self.assertNotIn("UV_PROJECT_ENVIRONMENT", sanitized_host)
            self.assertNotIn("VIRTUAL_ENV", sanitized_host)
            host_path_parts = {
                Path(p).resolve()
                for p in sanitized_host.get("PATH", "").split(os.pathsep)
                if p
            }
            self.assertNotIn((shared_host / "Scripts").resolve(), host_path_parts)


if __name__ == "__main__":
    unittest.main()
