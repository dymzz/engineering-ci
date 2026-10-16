"""Lock consistency and OSV security are independent, explicitly enabled checks."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

SCANNER_VERSION = "2.6.0"
SCANNER_ASSETS = {
    ("Linux", "x86_64"): (
        "osv-scanner_linux_amd64",
        "ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108",
    ),
    ("Windows", "AMD64"): (
        "osv-scanner_windows_amd64.exe",
        "e0ed7644118b717b028c249ee9d3515024e55e8510747ca08906eb96765354d6",
    ),
}
MAX_LOCK_BYTES = 5 * 1024 * 1024
MAX_RESULT_BYTES = 10 * 1024 * 1024
MAX_PACKAGES = 10_000


class SecurityError(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def _read(path, limit):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise SecurityError("input_too_large")
    return data


def _name(value, category="lock_format_invalid"):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", value
    ):
        raise SecurityError(category)
    return re.sub(r"[-_.]+", "-", value.lower())


def _run(args, cwd, timeout):
    # Raw subprocess logs remain ephemeral and are never artifacts or diagnostics.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.run(
            args, cwd=cwd, stdout=stdout, stderr=stderr, timeout=timeout, check=False
        )
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(
            args,
            process.returncode,
            stdout.read(65536).decode("utf-8", "replace"),
            stderr.read(65536).decode("utf-8", "replace"),
        )


def install_scanner(cache):
    """Install one pinned official asset; a cached binary is verified again."""
    asset = SCANNER_ASSETS.get((platform.system(), platform.machine()))
    if asset is None:
        raise SecurityError("scanner_platform_unsupported")
    name, expected = asset
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    hit = target.exists()
    if not hit:
        url = f"https://github.com/google/osv-scanner/releases/download/v{SCANNER_VERSION}/{name}"
        temporary = target.with_suffix(".partial")
        try:
            with (
                urllib.request.urlopen(url, timeout=30) as response,
                temporary.open("wb") as output,
            ):
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise SecurityError("scanner_download_too_large")
                    output.write(chunk)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                raise SecurityError("scanner_integrity_error")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        raise SecurityError("scanner_integrity_error")
    target.chmod(target.stat().st_mode | 0o111)
    return str(target), hit


def exceptions_policy(path, now):
    """Validate only native, finite OSV ID exceptions, without package overrides."""
    if path is None:
        return [], "IgnoredVulns = []\n"
    try:
        data = tomllib.loads(_read(path, 65536).decode("utf-8"))
    except (ValueError, UnicodeError):
        raise SecurityError("exceptions_invalid") from None
    if set(data) - {"IgnoredVulns"}:
        raise SecurityError("exceptions_invalid")
    entries = data.get("IgnoredVulns", [])
    if not isinstance(entries, list) or len(entries) > 100:
        raise SecurityError("exceptions_invalid")
    validated, config, seen = [], [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "reason", "ignoreUntil"}:
            raise SecurityError("exceptions_invalid")
        identifier, reason, expiry = entry["id"], entry["reason"], entry["ignoreUntil"]
        if not isinstance(identifier, str) or not re.fullmatch(
            r"(?:CVE-[0-9]{4}-[0-9]{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}|PYSEC-[0-9]{4}-[0-9]+)",
            identifier,
        ):
            raise SecurityError("exceptions_invalid")
        if (
            identifier in seen
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 1024
        ):
            raise SecurityError("exceptions_invalid")
        seen.add(identifier)
        if isinstance(expiry, dt.datetime):
            if expiry.tzinfo is None:
                raise SecurityError("exceptions_invalid")
        elif isinstance(expiry, dt.date):
            expiry = dt.datetime.combine(expiry, dt.time(), tzinfo=dt.UTC)
        else:
            raise SecurityError("exceptions_invalid")
        expiry = expiry.astimezone(dt.UTC)
        active = expiry > now
        validated.append(
            {
                "id": identifier,
                "reason": reason,
                "expires_at": expiry.isoformat(),
                "active": active,
            }
        )
        if active:
            config.append(
                f"[[IgnoredVulns]]\nid = {json.dumps(identifier)}\nreason = {json.dumps(reason, ensure_ascii=False)}\nignoreUntil = {expiry.isoformat()}\n"
            )
    return validated, "\n".join(config) or "IgnoredVulns = []\n"


def public_registry_policy(additional=()):
    """An exact disclosure policy; never guess whether a host is public."""
    if not isinstance(additional, (list, tuple)) or len(additional) > 10:
        raise SecurityError("public_index_config_invalid")
    accepted = {"https://pypi.org/simple"}
    for value in additional:
        if not isinstance(value, str) or len(value) > 2000:
            raise SecurityError("public_index_config_invalid")
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise SecurityError("public_index_config_invalid")
        if not re.fullmatch(r"[a-z0-9.-]+", parsed.hostname) or not parsed.path.rstrip(
            "/"
        ).endswith("/simple"):
            raise SecurityError("public_index_config_invalid")
        accepted.add(value.rstrip("/"))
    return accepted


def git_policy(path):
    """Approve exact HTTPS repository/commit identities, never package exclusions."""
    if path is None:
        return {}, None
    raw = _read(path, 65536)
    try:
        data = tomllib.loads(raw.decode("utf-8"))
        if (
            set(data) != {"schema_version", "git_sources"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
        ):
            raise ValueError
        entries = data["git_sources"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise ValueError
        result = {}
        for item in entries:
            if set(item) != {"package", "repository", "commit"}:
                raise ValueError
            name = _name(item["package"])
            repository, commit = item["repository"], item["commit"]
            if (
                name in result
                or not isinstance(repository, str)
                or not re.fullmatch(
                    r"https://[a-z0-9.-]+/[A-Za-z0-9_./-]+\.git", repository
                )
            ):
                raise ValueError
            if (
                "/../" in repository
                or not isinstance(commit, str)
                or not re.fullmatch(r"[0-9a-f]{40}", commit)
            ):
                raise ValueError
            result[name] = {"repository": repository, "commit": commit}
        return result, hashlib.sha256(raw).hexdigest()
    except (ValueError, KeyError, TypeError):
        raise SecurityError("git_policy_invalid") from None


def approved_git(name, source, project, policy):
    """Match the actual lock identity and any root declaration to the explicit pin."""
    if (
        set(source) != {"git"}
        or name not in policy
        or not isinstance(source["git"], str)
    ):
        return False
    expected = policy[name]
    parsed = urllib.parse.urlsplit(source["git"])
    repository = urllib.parse.urlunsplit(parsed._replace(query="", fragment=""))
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if (
        repository != expected["repository"]
        or parsed.fragment != expected["commit"]
        or query not in ({}, {"rev": [expected["commit"]]})
    ):
        return False
    declared = project.get("tool", {}).get("uv", {}).get("sources", {}).get(name)
    return declared is None or declared == {
        "git": repository,
        "rev": expected["commit"],
    }


def lock_inventory(project, lock_text, public_indexes=(), git_sources=None):
    """Inventory public lock entries; never resolve dependencies or match advisories."""
    try:
        parsed = tomllib.loads(lock_text)
    except ValueError:
        raise SecurityError("lock_format_invalid") from None
    if type(parsed.get("version")) is not int or parsed["version"] != 1:
        raise SecurityError("lock_format_unsupported")
    packages = parsed.get("package")
    if not isinstance(packages, list) or len(packages) > MAX_PACKAGES:
        raise SecurityError("lock_format_invalid")
    project_name = _name(project["project"]["name"])
    accepted_indexes = public_registry_policy(public_indexes)
    expected, unsupported, root_editable = set(), [], False
    git_sources, selected_git = git_sources or {}, []
    for package in packages:
        name, version = _name(package["name"]), package.get("version")
        source = package.get("source", {})
        if (
            not isinstance(version, str)
            or not version
            or len(version) > 200
            or not isinstance(source, dict)
        ):
            raise SecurityError("lock_format_invalid")
        if name == project_name and source in ({"virtual": "."}, {"editable": "."}):
            root_editable = source == {"editable": "."}
            continue
        if (
            isinstance(source.get("registry"), str)
            and source["registry"].rstrip("/") in accepted_indexes
            and set(source) == {"registry"}
        ):
            expected.add((name, version))
        elif approved_git(name, source, project, git_sources):
            selected_git.append(
                {"package": name, "version": version, **git_sources[name]}
            )
        else:
            unsupported.append(
                {
                    "package": name,
                    "version": version,
                    "reason": "non_public_pypi_source",
                }
            )
    # OSV's pinned extractor skips virtual ".", but includes editable ".".
    # Mark only the project's own editable node virtual in an ephemeral copy.
    # Verify that no third-party dependency metadata changed.
    if root_editable:
        blocks = lock_text.split("[[package]]")
        for i, block in enumerate(blocks[1:], 1):
            entry = tomllib.loads("[[package]]" + block)["package"][0]
            if _name(entry["name"]) == project_name and entry.get("source") == {
                "editable": "."
            }:
                blocks[i], count = re.subn(
                    r'(?m)^source[ \t]*=[ \t]*\{[ \t]*editable[ \t]*=[ \t]*"\."[ \t]*\}[ \t]*\r?$',
                    'source = { virtual = "." }',
                    block,
                )
                if count != 1:
                    raise SecurityError("local_project_exclusion_unavailable")
        lock_text = "[[package]]".join(blocks)
        for package in parsed["package"]:
            if _name(package["name"]) == project_name and package.get("source") == {
                "editable": "."
            }:
                package["source"] = {"virtual": "."}
        if tomllib.loads(lock_text) != parsed:
            raise SecurityError("local_project_exclusion_unavailable")
    if selected_git:
        # Native uv output labels Git entries PyPI; use explicit Git identities.
        # Move only approved Git entries to the native custom-lockfile input.
        names = {item["package"] for item in selected_git}
        parsed = tomllib.loads(lock_text)
        blocks = lock_text.split("[[package]]")
        lock_text = blocks[0] + "".join(
            "[[package]]" + block
            for block in blocks[1:]
            if _name(tomllib.loads("[[package]]" + block)["package"][0]["name"])
            not in names
        )
        parsed["package"] = [
            item for item in parsed["package"] if _name(item["name"]) not in names
        ]
        if tomllib.loads(lock_text) != parsed:
            raise SecurityError("git_scan_input_invalid")
    return expected, unsupported, lock_text, selected_git


def _json(data):
    def invalid_constant(value):
        raise SecurityError("scanner_response_invalid")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SecurityError("scanner_response_invalid")
            result[key] = value
        return result

    try:
        return json.loads(
            data, object_pairs_hook=unique, parse_constant=invalid_constant
        )
    except (ValueError, UnicodeError):
        raise SecurityError("scanner_response_invalid") from None


def scanner_result(raw, expected, exit_code, max_findings, git_sources=()):
    """Interpret the pinned all-packages contract, not terminal output."""
    if exit_code not in (0, 1):
        raise SecurityError(
            {
                129: "vulnerability_database_unavailable",
                128: "scanner_no_packages",
                130: "scanner_config_error",
            }.get(exit_code, "scanner_execution_error")
        )
    data = _json(raw)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("results"), list)
        or data.get("experimental_generic_findings")
    ):
        raise SecurityError("scanner_response_invalid")
    actual, findings, actual_git = set(), {}, set()
    expected_git = {(item["repository"], item["commit"]) for item in git_sources}
    for source in data["results"]:
        if not isinstance(source, dict) or not isinstance(source.get("packages"), list):
            raise SecurityError("scanner_response_invalid")
        source["source"] = {"path": "uv.lock", "type": "lockfile"}
        for item in source["packages"]:
            package = item["package"]
            is_git = package.get("ecosystem") == "GIT"
            if is_git:
                identity = (package.get("name"), package.get("commit"))
                if identity not in expected_git or package.get("version", "") != "":
                    raise SecurityError("scan_coverage_gap")
                actual_git.add(identity)
                name, version = identity
            else:
                name, version = (
                    _name(package["name"], "scanner_response_invalid"),
                    package["version"],
                )
                if package.get("ecosystem") != "PyPI" or not isinstance(version, str):
                    raise SecurityError("scanner_response_invalid")
                actual.add((name, version))
            vulnerabilities = item.get("vulnerabilities", [])
            if not isinstance(vulnerabilities, list):
                raise SecurityError("scanner_response_invalid")
            for vulnerability in vulnerabilities:
                identifier = vulnerability["id"]
                if (
                    not isinstance(identifier, str)
                    or not identifier
                    or len(identifier) > 200
                ):
                    raise SecurityError("scanner_response_invalid")
                fixed = set()
                for affected in vulnerability.get("affected", []):
                    affected_package = affected.get("package", {})
                    if (
                        affected_package.get("ecosystem") != "PyPI"
                        or _name(
                            affected_package.get("name"), "scanner_response_invalid"
                        )
                        != name
                    ):
                        continue
                    for affected_range in affected.get("ranges", []):
                        for event in affected_range.get("events", []):
                            if isinstance(event.get("fixed"), str):
                                fixed.add(event["fixed"])
                aliases = vulnerability.get("aliases", [])
                if not isinstance(aliases, list) or any(
                    not isinstance(alias, str) or len(alias) > 200 for alias in aliases
                ):
                    raise SecurityError("scanner_response_invalid")
                findings[(name, version, identifier)] = {
                    "package": name,
                    "version": version,
                    "id": identifier,
                    "aliases": aliases,
                    "fixed_versions": sorted(fixed),
                    "triggers_gate": True,
                }
                if is_git:
                    findings[(name, version, identifier)].update(
                        ecosystem="GIT", repository=name, commit=version
                    )
    if actual != expected or actual_git != expected_git:
        raise SecurityError("scan_coverage_gap")
    if bool(findings) != (exit_code == 1):
        raise SecurityError("scanner_exit_result_disagreement")
    findings = [findings[key] for key in sorted(findings)]
    return {
        "status": "failed" if findings else "passed",
        "dependencies_scanned": len(actual) + len(actual_git),
        "vulnerabilities": findings[:max_findings],
        "vulnerability_records": len(findings),
        "omitted_findings": max(0, len(findings) - max_findings),
    }, data


def scan(
    project,
    *,
    mode="off",
    scanner=None,
    uv="uv",
    exceptions=None,
    cache=None,
    max_findings=100,
    timeout=120,
    runner=_run,
    now=None,
    workspace_root=None,
    public_indexes=(),
    git_policy_path=None,
):
    started = time.perf_counter()
    fixed_now = now
    now = now or dt.datetime.now(dt.UTC)
    report = {
        "schema_version": 1,
        "status": "not_applicable",
        "scan_target": "uv.lock",
        "scan_scope": "public_pypi_locked_dependencies",
        "scope_note": "Potential lockfile findings across environments; not proof of installation or exploitability.",
        "database_mode": "online",
        "integrity": {
            "status": "not_applicable",
            "check": "uv lock --check",
            "exit_code": None,
        },
        "scanner": {
            "name": "osv-scanner",
            "version": SCANNER_VERSION,
            "status": "not_applicable",
            "exit_code": None,
        },
        "dependencies_scanned": None,
        "vulnerability_records": 0,
        "vulnerabilities": [],
        "omitted_findings": 0,
        "unsupported_dependencies": [],
        "exceptions": [],
        "diagnostics": [],
        "scanner_binary_cache_hit": None,
        "timings": {
            "lockfile_read_seconds": 0,
            "integrity_check_seconds": 0,
            "scanner_initialization_seconds": 0,
            "vulnerability_query_seconds": 0,
        },
    }
    raw_result, phase, stage = None, "configuration", started
    valid_mode = isinstance(mode, str) and mode in {"off", "report", "enforce"}
    mode = mode if valid_mode else "invalid"
    try:
        if not valid_mode:
            raise SecurityError("mode_invalid")
        if type(max_findings) is not int or not 1 <= max_findings <= 1000:
            raise SecurityError("finding_limit_invalid")
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise SecurityError("timeout_invalid")
        if mode != "off":
            report["public_registry_policy"] = sorted(
                public_registry_policy(public_indexes)
            )
            project = Path(project).resolve()
            if workspace_root is not None:
                workspace = Path(workspace_root).resolve()
                if not project.is_relative_to(workspace):
                    raise SecurityError("project_outside_workspace")
                if exceptions is not None and not Path(
                    exceptions
                ).resolve().is_relative_to(workspace):
                    raise SecurityError("exceptions_outside_workspace")
                if git_policy_path is not None and not Path(
                    git_policy_path
                ).resolve().is_relative_to(workspace):
                    raise SecurityError("git_policy_outside_workspace")
            policy, policy_digest = git_policy(git_policy_path)
            if policy:
                report["git_policy_sha256"] = policy_digest
            phase = "lockfile"
            stage = time.perf_counter()
            if not (project / "pyproject.toml").is_file():
                raise SecurityError("project_missing")
            if not (project / "uv.lock").is_file():
                raise SecurityError("lock_missing")
            metadata_bytes = _read(project / "pyproject.toml", 1024 * 1024)
            lock_bytes = _read(project / "uv.lock", MAX_LOCK_BYTES)
            try:
                metadata = tomllib.loads(metadata_bytes.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise SecurityError("project_format_invalid") from None
            expected, unsupported, scanner_lock, git_sources = lock_inventory(
                metadata, lock_bytes.decode("utf-8"), public_indexes, policy
            )
            if git_sources:
                report["scan_scope"] = "public_pypi_and_approved_git_commits"
                report["git_sources"] = [
                    {
                        **item,
                        "source_integrity": "passed",
                        "advisory_query": "not_executed",
                    }
                    for item in git_sources
                ]
                report["scope_note"] += (
                    " Git queries match known commit advisories; they are not source-code audits or proof of advisory availability for a private repository."
                )
            report["unsupported_dependencies"] = unsupported[:100]
            report["unsupported_dependency_count"] = len(unsupported)
            report["omitted_unsupported_dependencies"] = max(0, len(unsupported) - 100)
            report["lock_sha256"] = hashlib.sha256(lock_bytes).hexdigest()
            report["timings"]["lockfile_read_seconds"] = time.perf_counter() - stage
            stage = time.perf_counter()
            phase = "integrity"
            checked = runner(
                [uv, "lock", "--check", "--project", str(project)], project, timeout
            )
            report["timings"]["integrity_check_seconds"] = time.perf_counter() - stage
            report["integrity"]["exit_code"] = checked.returncode
            if checked.returncode != 0:
                outdated = checked.returncode in (1, 2) and re.search(
                    r"lockfile(?: at .+)? needs to be updated", checked.stderr.lower()
                )
                report["integrity"]["status"] = "failed" if outdated else "error"
                raise SecurityError(
                    "lock_outdated" if outdated else "integrity_check_error"
                )
            report["integrity"]["status"] = "passed"
            if unsupported:
                raise SecurityError("scan_coverage_gap")
            if (
                _read(project / "uv.lock", MAX_LOCK_BYTES) != lock_bytes
                or _read(project / "pyproject.toml", 1024 * 1024) != metadata_bytes
            ):
                raise SecurityError("project_changed_during_check")
            report["exceptions"], native_config = exceptions_policy(exceptions, now)
            if not expected and not git_sources:
                report["dependencies_scanned"] = 0
                report["diagnostics"].append("no_third_party_dependencies")
            else:
                stage = time.perf_counter()
                phase = "scanner_initialization"
                if scanner is None:
                    scanner, report["scanner_binary_cache_hit"] = install_scanner(
                        cache or project / ".cache/engineering-ci-security"
                    )
                else:
                    asset = SCANNER_ASSETS.get((platform.system(), platform.machine()))
                    if asset is None:
                        raise SecurityError("scanner_platform_unsupported")
                    if (
                        hashlib.sha256(Path(scanner).read_bytes()).hexdigest()
                        != asset[1]
                    ):
                        raise SecurityError("scanner_integrity_error")
                version = runner([str(scanner), "--version"], project, timeout)
                if version.returncode != 0 or not re.search(
                    r"(?m)^osv-scanner version: "
                    + re.escape(SCANNER_VERSION)
                    + r"\s*$",
                    version.stdout,
                ):
                    raise SecurityError("scanner_version_mismatch")
                report["timings"]["scanner_initialization_seconds"] = (
                    time.perf_counter() - stage
                )
                with tempfile.TemporaryDirectory(
                    prefix="engineering-ci-security-"
                ) as temp:
                    temp = Path(temp)
                    (temp / "uv.lock").write_bytes(scanner_lock.encode("utf-8"))
                    (temp / "osv-scanner.toml").write_text(
                        native_config, encoding="utf-8"
                    )
                    lockfiles = (
                        ["--lockfile=" + str(temp / "uv.lock")] if expected else []
                    )
                    if git_sources:
                        native_git = {
                            "results": [
                                {
                                    "packages": [
                                        {
                                            "package": {
                                                "name": repository,
                                                "commit": commit,
                                            }
                                        }
                                        for repository, commit in sorted(
                                            {
                                                (item["repository"], item["commit"])
                                                for item in git_sources
                                            }
                                        )
                                    ]
                                }
                            ]
                        }
                        (temp / "osv-scanner.json").write_text(
                            json.dumps(native_git), encoding="utf-8"
                        )
                        lockfiles.append(
                            "--lockfile=osv-scanner:" + str(temp / "osv-scanner.json")
                        )
                    stage = time.perf_counter()
                    phase = "vulnerability_query"
                    for item in report.get("git_sources", []):
                        item["advisory_query"] = "unavailable"
                    result = runner(
                        [
                            str(scanner),
                            "scan",
                            "source",
                            *lockfiles,
                            "--config=" + str(temp / "osv-scanner.toml"),
                            "--format=json",
                            "--all-packages",
                            "--output-file=" + str(temp / "scanner.json"),
                        ],
                        project,
                        timeout,
                    )
                    report["timings"]["vulnerability_query_seconds"] = (
                        time.perf_counter() - stage
                    )
                    report["scanner"]["exit_code"] = result.returncode
                    # Preserve error classification before considering any JSON.
                    if result.returncode not in (0, 1):
                        scanner_result(
                            b"", expected, result.returncode, max_findings, git_sources
                        )
                    if not (temp / "scanner.json").is_file():
                        raise SecurityError("scanner_response_missing")
                    parsed, raw_result = scanner_result(
                        _read(temp / "scanner.json", MAX_RESULT_BYTES),
                        expected,
                        result.returncode,
                        max_findings,
                        git_sources,
                    )
                    report.update(parsed)
                    report["scanner"]["status"] = "complete"
                    for item in report.get("git_sources", []):
                        item["advisory_query"] = "complete"
                current = fixed_now or dt.datetime.now(dt.UTC)
                if any(
                    item["active"]
                    and dt.datetime.fromisoformat(item["expires_at"]) <= current
                    for item in report["exceptions"]
                ):
                    raise SecurityError("exception_expired_during_scan")
                if (
                    _read(project / "uv.lock", MAX_LOCK_BYTES) != lock_bytes
                    or _read(project / "pyproject.toml", 1024 * 1024) != metadata_bytes
                ):
                    raise SecurityError("project_changed_during_scan")
                if (
                    git_policy_path is not None
                    and hashlib.sha256(_read(git_policy_path, 65536)).hexdigest()
                    != policy_digest
                ):
                    raise SecurityError("git_policy_changed_during_scan")
    except SecurityError as error:
        report["status"] = "error"
        report["diagnostics"].append(error.category)
    except subprocess.TimeoutExpired:
        report["status"] = "error"
        report["diagnostics"].append("execution_timeout")
    except FileNotFoundError:
        report["status"] = "error"
        report["diagnostics"].append(
            "tool_missing" if phase != "lockfile" else "input_missing"
        )
    except (OSError, urllib.error.URLError):
        report["status"] = "error"
        report["diagnostics"].append("tool_or_network_unavailable")
    except (ValueError, KeyError, TypeError, AttributeError):
        report["status"] = "error"
        report["diagnostics"].append("input_or_scanner_response_invalid")
    if report["status"] == "error":
        report["scanner"]["status"] = (
            "error"
            if phase in {"scanner_initialization", "vulnerability_query"}
            else "not_applicable"
        )
        if report["integrity"]["status"] == "not_applicable":
            report["integrity"]["status"] = "error"
        timing_key = {
            "lockfile": "lockfile_read_seconds",
            "integrity": "integrity_check_seconds",
            "scanner_initialization": "scanner_initialization_seconds",
            "vulnerability_query": "vulnerability_query_seconds",
        }.get(phase)
        if timing_key and not report["timings"][timing_key]:
            report["timings"][timing_key] = time.perf_counter() - stage
    report["phase"] = phase
    passed = report["status"] in {"passed", "not_applicable"}
    blocking = mode == "enforce" or mode not in {"off", "report", "enforce"}
    report["gate"] = {
        "mode": mode,
        "passed": None if mode == "off" else passed,
        "blocking": blocking,
        "exit_code": int(blocking and not passed),
    }
    report["timings"]["total_seconds"] = time.perf_counter() - started
    return report, raw_result


def summary(report):
    def safe(value):
        return (
            str(value)
            .replace("|", "\\|")
            .replace("\n", " ")
            .replace("<", "&lt;")
            .replace(">", "&gt;")[:300]
        )

    rows = [
        ("Scan target", report["scan_target"]),
        ("Scope", report["scan_scope"]),
        ("Lock integrity", report["integrity"]["status"]),
        ("Scanner status", report["scanner"]["status"]),
        ("Database mode", report["database_mode"]),
        ("Dependencies scanned", report["dependencies_scanned"]),
        ("Known vulnerability records", report["vulnerability_records"]),
        (
            "Active exceptions configured",
            sum(item["active"] for item in report["exceptions"]),
        ),
        (
            "Expired exceptions",
            sum(not item["active"] for item in report["exceptions"]),
        ),
        ("Security policy", report["status"]),
        (
            "CI gate",
            "DISABLED"
            if report["gate"]["mode"] == "off"
            else "BLOCK"
            if report["gate"]["exit_code"]
            else "NON-BLOCKING"
            if report["gate"]["mode"] == "report"
            else "PASS",
        ),
    ]
    lines = ["## Dependency Security Summary", "", "| Metric | Result |", "|---|---|"]
    lines.extend(f"| {safe(key)} | {safe(value)} |" for key, value in rows)
    lines.extend(["", report["scope_note"], ""])
    for finding in report["vulnerabilities"][:20]:
        lines.append(
            f"- {safe(finding['package'])} {safe(finding['version'])}: {safe(finding['id'])}; fixed versions in OSV record: {safe(', '.join(finding['fixed_versions']) or 'not provided')}"
        )
    omitted = report["vulnerability_records"] - min(20, len(report["vulnerabilities"]))
    if omitted:
        lines.append(
            f"- {omitted} additional records omitted here; see scanner.json for full native results."
        )
    for diagnostic in report["diagnostics"]:
        lines.append(f"- Diagnostic: {safe(diagnostic)}")
    for dependency in report["unsupported_dependencies"][:20]:
        lines.append(
            f"- Coverage gap: {safe(dependency['package'])} {safe(dependency['version'])}; {dependency['reason']}"
        )
    return "\n".join(lines) + "\n"


def write_bundle(output, report, raw_result):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "report.json": json.dumps(report, indent=2),
        "summary.md": summary(report),
        "diagnostics.json": json.dumps(
            {
                "categories": report["diagnostics"],
                "integrity": report["integrity"],
                "scanner": report["scanner"],
                "timings": report["timings"],
            },
            indent=2,
        ),
    }
    if raw_result is not None:
        files["scanner.json"] = json.dumps(raw_result, indent=2)
    else:
        files["scanner.json"] = json.dumps(
            {"available": False, "reason": report["diagnostics"]}, indent=2
        )
    for name, content in files.items():
        temporary = output / (name + ".tmp")
        temporary.write_text(content + "\n", encoding="utf-8")
        os.replace(temporary, output / name)
