from pathlib import Path

import pytest

from depforecast.manifest import (
    ManifestError,
    load_dependencies,
    read_lockfile,
    read_package_json,
)
from depforecast.models import DepKind

FIXTURES = Path(__file__).parent / "fixtures" / "manifests"


def test_direct_deps_all_sections() -> None:
    deps = {d.name: d for d in read_package_json(FIXTURES / "basic" / "package.json")}
    assert set(deps) == {"express", "@scope/util", "lodash", "jest", "react", "fsevents"}
    assert deps["jest"].kind is DepKind.DEV
    assert deps["react"].kind is DepKind.PEER
    assert deps["fsevents"].kind is DepKind.OPTIONAL
    assert all(d.direct for d in deps.values())


def test_prod_wins_over_dev_for_duplicates() -> None:
    deps = {d.name: d for d in read_package_json(FIXTURES / "basic" / "package.json")}
    assert deps["express"].kind is DepKind.PROD
    assert deps["express"].spec == "^4.18.2"


def test_npm_alias_resolves_to_real_package() -> None:
    deps = {d.name: d for d in read_package_json(FIXTURES / "basic" / "package.json")}
    assert "my-lodash" not in deps
    assert deps["lodash"].spec == "^4.17.21"


def test_local_specs_are_skipped() -> None:
    names = {d.name for d in read_package_json(FIXTURES / "basic" / "package.json")}
    assert "local-thing" not in names
    assert "shared" not in names


def test_lockfile_v3() -> None:
    lock = read_lockfile(FIXTURES / "basic" / "package-lock.json")
    assert lock["express"] == "4.18.2"
    assert lock["lodash"] == "4.17.21"
    assert lock["@scope/util"] == "2.1.3"
    assert "debug" in lock  # nested and top-level copies collapse to one entry
    assert "shared" not in lock  # workspace link
    assert "demo-app" not in lock  # the root project itself


def test_lockfile_v1() -> None:
    lock = read_lockfile(FIXTURES / "lock_v1" / "package-lock.json")
    assert lock == {"request": "2.88.2", "uuid": "3.4.0", "tough-cookie": "2.5.0"}


def test_load_direct_only_by_default() -> None:
    deps = load_dependencies(FIXTURES / "basic")
    assert all(d.direct for d in deps)
    assert "body-parser" not in {d.name for d in deps}


def test_load_transitive_adds_lockfile_packages() -> None:
    deps = {d.name: d for d in load_dependencies(FIXTURES / "basic", transitive=True)}
    assert deps["express"].direct
    assert not deps["body-parser"].direct
    assert not deps["debug"].direct


def test_missing_package_json(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not found"):
        load_dependencies(tmp_path)


def test_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{ not json")
    with pytest.raises(ManifestError, match="cannot read"):
        load_dependencies(tmp_path)


def test_missing_lockfile_with_transitive(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"a": "1"}}')
    with pytest.raises(ManifestError, match="package-lock.json not found"):
        load_dependencies(tmp_path, transitive=True)
