import json
from pathlib import Path

from tests.helpers import (
    collect_bug_validation_statuses,
    collect_function_ids,
    collect_verdicts,
    iter_json_files,
    load_json,
    scenario_summary,
    setup_workspace,
    GOLDEN,
)


def _files_equal(dir_a: Path, dir_b: Path):
    """Recursively compare two directories by relative file paths and contents."""
    files_a = {f.relative_to(dir_a): f.read_bytes() for f in dir_a.rglob("*") if f.is_file()}
    files_b = {f.relative_to(dir_b): f.read_bytes() for f in dir_b.rglob("*") if f.is_file()}
    return files_a == files_b


def test_stage_01_setup(mock_backend, tmp_path):
    """Stage 1: generate phases.json and domain context."""
    proj = setup_workspace(tmp_path)
    work_dir = proj / "fm_agent"
    repo_root = Path(__file__).parent.parent

    from src.pipeline_setup import _run_setup_extract
    _run_setup_extract(
        str(proj),
        str(work_dir),
        str(repo_root),
        resume=False,
        backend=mock_backend,
    )

    assert (work_dir / "phases.json").exists()
    assert load_json(work_dir / "phases.json") == load_json(GOLDEN / "phases.json")
    assert _files_equal(
        work_dir / "spec_prompts" / "domain_context",
        GOLDEN / "domain_context",
    )


def test_stage_03_extraction(mock_backend, tmp_path):
    """Stage 3: extract functions from source files."""
    proj = setup_workspace(tmp_path, include_golden=["phases.json", "domain_context"])
    work_dir = proj / "fm_agent"

    from src.extract import run_extraction
    run_extraction(str(proj), work_dir=str(work_dir), force=True, verbose=False)

    expected = collect_function_ids(GOLDEN / "extracted_functions")
    actual = collect_function_ids(work_dir / "extracted_functions")
    assert actual == expected, (
        "Extracted function set differs from golden. "
        f"Missing: {expected - actual}; Extra: {actual - expected}"
    )


def test_stage_06_specgen(mock_backend, tmp_path):
    """Stage 6a: generate specs for all extracted functions."""
    proj = setup_workspace(tmp_path)

    from main import run_pipeline
    run_pipeline(str(proj), only_spec=True, backend=mock_backend)

    expected = collect_function_ids(GOLDEN / "extracted_functions")
    actual = collect_function_ids(proj / "fm_agent" / "extracted_functions")
    assert actual == expected, (
        "Extracted function set differs from golden. "
        f"Missing: {expected - actual}; Extra: {actual - expected}"
    )


def _golden_verified_file_list():
    files = []
    for f in (GOLDEN / "logic_verification_results").rglob("*.json"):
        rel = f.relative_to(GOLDEN / "logic_verification_results")
        func_id = str(rel.with_suffix("")).replace("/", "::")
        file_slug, func_name = func_id.split("::")
        matches = list((GOLDEN / "extracted_functions" / file_slug).glob(f"{func_name}.*"))
        if matches:
            ext = matches[0].suffix
            files.append(f"{file_slug}/{func_name}{ext}")
    return files


def test_stage_06_verify(mock_backend, tmp_path):
    """Stage 6b: verify pre-specced functions."""
    proj = setup_workspace(tmp_path, include_golden=["extracted_functions"])
    work_dir = proj / "fm_agent"
    input_dir = work_dir / "extracted_functions"
    output_dir = work_dir / "logic_verification_results"
    output_dir.mkdir(exist_ok=True)

    from src.verification import streaming_reasoner
    streaming_reasoner(
        str(input_dir),
        str(output_dir),
        file_list=_golden_verified_file_list(),
        proj_dir=str(proj),
        work_dir=str(work_dir),
        backend=mock_backend,
    )

    expected = collect_verdicts(GOLDEN / "logic_verification_results")
    actual = collect_verdicts(output_dir)
    assert actual == expected, (
        "Verification verdicts differ from golden. "
        f"Differing keys: {set(expected.items()) ^ set(actual.items())}"
    )
    _assert_verification_schema(output_dir)


def test_stage_06_bugval(mock_backend, tmp_path):
    """Stage 6c: validate each MISMATCH as a candidate bug."""
    proj = setup_workspace(
        tmp_path, include_golden=["extracted_functions", "logic_verification_results"]
    )
    work_dir = proj / "fm_agent"

    from src.verification import _validate_single_bug, _generate_validation_summary

    verdicts = collect_verdicts(work_dir / "logic_verification_results")
    for fid, verdict in verdicts.items():
        if verdict != "MISMATCH":
            continue
        rel = fid.replace("::", "/")
        result_json_rel = f"fm_agent/logic_verification_results/{rel}.json"
        _validate_single_bug(
            result_json_rel, str(proj), str(work_dir),
            resume=False, backend=mock_backend,
        )

    _generate_validation_summary(str(work_dir))

    summary_path = work_dir / "bug_validation" / "summary.json"
    assert summary_path.exists()
    summary = load_json(summary_path)
    for key, expected in scenario_summary().items():
        assert summary[key] == expected

    expected_statuses = collect_bug_validation_statuses(GOLDEN / "bug_validation")
    actual_statuses = collect_bug_validation_statuses(work_dir / "bug_validation")
    assert actual_statuses == expected_statuses


def _assert_verification_schema(results_dir: Path):
    for path in iter_json_files(results_dir):
        data = load_json(path)
        assert isinstance(data.get("function"), str)
        assert data.get("verdict") in {"MATCH", "MISMATCH", "ERROR"}
        if data["verdict"] == "MISMATCH":
            assert isinstance(data.get("gaps"), dict)
        if data["verdict"] == "ERROR":
            assert isinstance(data.get("error"), str)
