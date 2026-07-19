import json
import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_SCENARIO_PATH = Path(__file__).parent / "scenarios" / "replay.json"


def load_scenario(path: Path | str = DEFAULT_SCENARIO_PATH):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent.parent.parent
    data["_path"] = path
    fixture_dir = _resolve_fixture_dir(base, data.get("fixture_dir"))
    data["fixture_dir"] = fixture_dir
    data["project_src"] = _resolve_scenario_path(
        base, _format_scenario_path(data["project_src"], fixture_dir)
    )
    data["golden"] = _resolve_scenario_path(
        base, _format_scenario_path(data["golden"], fixture_dir)
    )
    return data


def _resolve_fixture_dir(base: Path, spec):
    if isinstance(spec, dict):
        env_name = spec.get("env")
        value = os.environ.get(env_name, "") if env_name else ""
        if not value:
            value = spec.get("default", "")
    else:
        value = spec or ""
    return _resolve_scenario_path(base, value)


def _format_scenario_path(value: str, fixture_dir: Path):
    return value.format(fixture_dir=str(fixture_dir))


def _resolve_scenario_path(base: Path, value: str):
    path = Path(value)
    return path if path.is_absolute() else base / path


DEFAULT_SCENARIO = load_scenario()
GOLDEN = Path(DEFAULT_SCENARIO["golden"])


def scenario_summary(scenario=None):
    scenario = scenario or DEFAULT_SCENARIO
    return scenario["assertions"]["bug_validation_summary"]


def setup_workspace(tmp_path, include_golden=None, scenario=None):
    """Copy the scenario project source and optionally some golden outputs into a temp workspace."""
    scenario = scenario or DEFAULT_SCENARIO
    project_src = Path(scenario["project_src"])
    golden = Path(scenario["golden"])
    proj = tmp_path / scenario["name"]
    shutil.copytree(
        project_src,
        proj,
        ignore=shutil.ignore_patterns("fm_agent", ".git", ".omo"),
    )
    fm = proj / "fm_agent"
    fm.mkdir(exist_ok=True)
    if include_golden:
        for name in include_golden:
            src = golden / name
            dst = fm / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    return proj


def collect_function_ids(extracted_dir: Path):
    ids = set()
    if not extracted_dir.exists():
        return ids
    for func_dir in extracted_dir.iterdir():
        if not func_dir.is_dir():
            continue
        file_slug = func_dir.name
        for f in func_dir.iterdir():
            if f.is_file():
                ids.add(f"{file_slug}::{f.stem}")
    return ids


def collect_verdicts(verify_dir: Path):
    verdicts = {}
    if not verify_dir.exists():
        return verdicts
    for func_dir in verify_dir.iterdir():
        if not func_dir.is_dir():
            continue
        file_slug = func_dir.name
        for f in func_dir.iterdir():
            if f.suffix == ".json":
                data = json.loads(f.read_text(encoding="utf-8"))
                verdicts[f"{file_slug}::{f.stem}"] = data.get("verdict")
    return verdicts


def collect_bug_validation_statuses(validation_dir: Path):
    statuses = {}
    if not validation_dir.exists():
        return statuses
    for f in validation_dir.glob("*.result.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        statuses[f.name.removesuffix(".result.json")] = data.get(
            "confirmation_status"
        )
    return statuses


def iter_json_files(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*.json"):
        if path.is_file():
            yield path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
