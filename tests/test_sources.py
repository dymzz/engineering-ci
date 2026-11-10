"""Source manifest reconciliation, set diff and Git attribute contracts."""

import hashlib
import json
import subprocess

from engineering_ci import sources


def make(tmp_path, *, entries, extra=(), config_extra=""):
    root = tmp_path / "repo"
    (root / "data/acquired").mkdir(parents=True)
    manifest = root / "data/catalog/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(entries), encoding="utf-8")
    for path, content in extra:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (root / "ci-sources.toml").write_text(
        f"""schema_version = 1
manifest = "data/catalog/manifest.json"
acquired_roots = ["data/acquired"]
exempt_paths = ["data/acquired/README.md"]
environment_prefixes = ["sources/environment/"]
require_text_unset = true
{config_extra}
""",
        encoding="utf-8",
    )
    return root


def entry(path, content, *, sha=None, size=None):
    return {
        "path": path,
        "sha256": sha or hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content) if size is None else size,
    }


class Attributes:
    def __init__(self, value="unset", code=0):
        self.value = value
        self.code = code
        self.argv = None

    def __call__(self, argv, root):
        self.argv = argv
        paths = argv[argv.index("--") + 1 :]
        if self.code:
            return subprocess.CompletedProcess(argv, self.code, "", "git failed")
        output = "".join(f"{path}\x00text\x00{self.value}\x00" for path in paths)
        return subprocess.CompletedProcess(argv, 0, output, "")


def check(root, attributes=None):
    return sources.reconcile(
        sources.load_config(root / "ci-sources.toml"),
        root=root,
        runner=attributes or Attributes(),
    )


def codes(report):
    return [failure["code"] for failure in report.failures]


def test_valid_manifest_passes(tmp_path):
    content = b"source bytes"
    root = make(
        tmp_path,
        entries=[entry("data/acquired/a.bin", content)],
        extra=[("data/acquired/a.bin", content)],
    )
    report = check(root)
    assert report.status == "passed"
    assert report.counts["checked"] == 1
    assert report.failures == ()


def test_missing_file_fails(tmp_path):
    root = make(tmp_path, entries=[entry("data/acquired/a.bin", b"x")])
    report = check(root)
    assert report.status == "failed"
    assert "missing" in codes(report)


def test_unregistered_acquired_file_fails(tmp_path):
    content = b"x"
    root = make(
        tmp_path,
        entries=[entry("data/acquired/a.bin", content)],
        extra=[("data/acquired/a.bin", content), ("data/acquired/other.bin", b"y")],
    )
    report = check(root)
    assert "unregistered" in codes(report)


def test_duplicate_and_conflicting_paths_are_distinct(tmp_path):
    content = b"x"
    root = make(
        tmp_path,
        entries=[
            entry("data/acquired/a.bin", content),
            entry("data/acquired/a.bin", content),
        ],
        extra=[("data/acquired/a.bin", content)],
    )
    assert "duplicate" in codes(check(root))
    other = make(
        tmp_path / "conflict",
        entries=[
            entry("data/acquired/a.bin", content),
            entry("data/acquired/a.bin", b"y"),
        ],
        extra=[("data/acquired/a.bin", content)],
    )
    assert "conflict" in codes(check(other))


def test_digest_and_size_mismatches_fail(tmp_path):
    content = b"x"
    root = make(
        tmp_path,
        entries=[entry("data/acquired/a.bin", content, sha="0" * 64, size=999)],
        extra=[("data/acquired/a.bin", content)],
    )
    found = set(codes(check(root)))
    assert {"digest", "size"} <= found


def test_unsafe_manifest_path_is_refused(tmp_path):
    root = make(tmp_path, entries=[entry("../escape", b"x")])
    assert "path_invalid" in codes(check(root))


def test_environment_coordinate_may_be_absent(tmp_path):
    root = make(
        tmp_path,
        entries=[
            {
                "path": "sources/environment/a.bin",
                "sha256": None,
                "size_bytes": None,
            }
        ],
    )
    report = check(root)
    assert "missing" not in codes(report)


def test_environment_entry_with_claimed_bytes_is_refused(tmp_path):
    root = make(
        tmp_path,
        entries=[entry("sources/environment/a.bin", b"x")],
    )
    assert "environment_has_digest" in codes(check(root))


def test_path_outside_acquired_roots_is_refused(tmp_path):
    root = make(tmp_path, entries=[entry("src/other.py", b"x")])
    assert "path_outside_roots" in codes(check(root))


def test_null_path_without_evidence_is_allowed_but_with_evidence_is_refused(tmp_path):
    allowed = make(
        tmp_path / "allowed",
        entries=[{"local_path": None, "sha256": None, "size_bytes": None}],
        config_extra='path_field = "local_path"',
    )
    assert "path_missing_evidence" not in codes(check(allowed))
    refused = make(
        tmp_path / "refused",
        entries=[{"local_path": None, "sha256": "0" * 64, "size_bytes": 1}],
        config_extra='path_field = "local_path"',
    )
    assert "path_missing_evidence" in codes(check(refused))


def test_exempt_extra_file_is_allowed(tmp_path):
    content = b"x"
    root = make(
        tmp_path,
        entries=[entry("data/acquired/a.bin", content)],
        extra=[
            ("data/acquired/a.bin", content),
            ("data/acquired/README.md", b"documentation"),
        ],
    )
    assert "unregistered" not in codes(check(root))


def test_wrong_git_attribute_fails(tmp_path):
    content = b"x"
    root = make(
        tmp_path,
        entries=[entry("data/acquired/a.bin", content)],
        extra=[("data/acquired/a.bin", content)],
    )
    report = check(root, Attributes(value="set"))
    assert "attributes" in codes(report)


def test_missing_git_context_is_a_failure(tmp_path):
    content = b"x"
    root = make(
        tmp_path,
        entries=[entry("data/acquired/a.bin", content)],
        extra=[("data/acquired/a.bin", content)],
    )
    report = check(root, Attributes(code=1))
    assert "attributes_unavailable" in codes(report)


def test_cli_emits_json_and_preserves_failure(tmp_path, capsys):
    root = make(tmp_path, entries=[entry("data/acquired/a.bin", b"x")])
    assert (
        sources.main(
            [
                "--root",
                str(root),
                "--config",
                str(root / "ci-sources.toml"),
                "--json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "engineering-ci-sources-report"
    assert payload["status"] == "failed"


def test_domain_field_projection_is_configurable(tmp_path):
    content = b"x"
    root = make(
        tmp_path,
        entries=[
            {
                "source_path": "data/acquired/a.bin",
                "digest": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        ],
        extra=[("data/acquired/a.bin", content)],
        config_extra=(
            'path_field = "source_path"\nsha256_field = "digest"\nsize_field = "bytes"'
        ),
    )
    report = check(root)
    assert report.status == "passed"
    assert report.counts["checked"] == 1
