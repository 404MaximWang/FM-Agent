import json

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


def test_replay_full_pipeline(mock_backend, tmp_path):
    """Run the full FM-Agent pipeline against a replay fixture."""
    proj = setup_workspace(tmp_path)

    from main import run_pipeline
    run_pipeline(str(proj), only_spec=False, backend=mock_backend)

    fm = proj / "fm_agent"

    expected_ids = collect_function_ids(GOLDEN / "extracted_functions")
    actual_ids = collect_function_ids(fm / "extracted_functions")
    assert actual_ids == expected_ids, (
        "Extracted function set differs from golden. "
        f"Missing: {expected_ids - actual_ids}; Extra: {actual_ids - expected_ids}"
    )

    expected_verdicts = collect_verdicts(GOLDEN / "logic_verification_results")
    actual_verdicts = collect_verdicts(fm / "logic_verification_results")
    assert actual_verdicts == expected_verdicts, (
        "Verification verdicts differ from golden. "
        f"Differing keys: "
        f"{set(expected_verdicts.items()) ^ set(actual_verdicts.items())}"
    )
    _assert_verification_schema(fm / "logic_verification_results")

    summary_path = fm / "bug_validation" / "summary.json"
    assert summary_path.exists(), "bug_validation/summary.json was not produced"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for key, expected in scenario_summary().items():
        assert summary[key] == expected

    expected_statuses = collect_bug_validation_statuses(GOLDEN / "bug_validation")
    actual_statuses = collect_bug_validation_statuses(fm / "bug_validation")
    assert actual_statuses == expected_statuses
    mock_backend.assert_workflow()


def _assert_verification_schema(results_dir):
    for path in iter_json_files(results_dir):
        data = load_json(path)
        assert isinstance(data.get("function"), str)
        assert data.get("verdict") in {"MATCH", "MISMATCH", "ERROR"}
        if data["verdict"] == "MISMATCH":
            assert isinstance(data.get("gaps"), dict)
        if data["verdict"] == "ERROR":
            assert isinstance(data.get("error"), str)
