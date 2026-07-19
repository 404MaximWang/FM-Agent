import json
import shutil
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


def _stage_domain_context(work_dir: Path):
    target = work_dir / "spec_prompts" / "domain_context"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(GOLDEN / "domain_context", target)


def test_setup_writes_source_and_modules_manifest(mock_backend, tmp_path):
    """Setup writes source/module manifests and domain context."""
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

    assert (work_dir / "source_files.json").exists()
    assert (work_dir / "modules.json").exists()
    assert load_json(work_dir / "source_files.json") == load_json(GOLDEN / "source_files.json")
    assert load_json(work_dir / "modules.json") == load_json(GOLDEN / "modules.json")
    assert _files_equal(
        work_dir / "spec_prompts" / "domain_context",
        GOLDEN / "domain_context",
    )


def test_extraction_uses_source_manifest(mock_backend, tmp_path):
    """Extract functions from source files declared by setup manifests."""
    proj = setup_workspace(
        tmp_path,
        include_golden=["source_files.json", "modules.json", "domain_context"],
    )
    work_dir = proj / "fm_agent"

    from src.extract import run_extraction
    run_extraction(str(proj), work_dir=str(work_dir), force=True, verbose=False)

    expected = collect_function_ids(GOLDEN / "extracted_functions")
    actual = collect_function_ids(work_dir / "extracted_functions")
    assert actual == expected, (
        "Extracted function set differs from golden. "
        f"Missing: {expected - actual}; Extra: {actual - expected}"
    )


def test_global_layers_drive_spec_generation(mock_backend, tmp_path):
    """Generate specs for all extracted functions using global layers."""
    proj = setup_workspace(tmp_path)

    from main import run_pipeline
    run_pipeline(str(proj), only_spec=True, backend=mock_backend)

    fm = proj / "fm_agent"
    topdown = load_json(fm / "spec_prompts" / "topdown_layers.json")
    first_function = topdown["layers"][0]["functions"][0]
    assert "callers" in first_function
    assert "callees" in first_function
    assert not any(key.startswith("phase") for key in first_function)

    project_name = load_json(GOLDEN / "modules.json")["project"]
    manifest = load_json(
        fm / "spec_prompts" / f"batch_prompts_{project_name}" / "manifest.json"
    )
    assert "phase" not in manifest
    prompt_batch = next(
        (batch for batch in manifest["batches"] if batch.get("num_pending", 0) > 0),
        None,
    )
    assert prompt_batch is not None
    batch_prompt = fm / "spec_prompts" / f"batch_prompts_{project_name}" / prompt_batch["file"]
    prompt_text = batch_prompt.read_text(encoding="utf-8")
    assert "## MODULE CONTEXT" in prompt_text
    modules_by_name = {
        module["name"]: module
        for module in load_json(GOLDEN / "modules.json")["modules"]
    }
    for module_name in prompt_batch["module_names"]:
        description = modules_by_name[module_name]["description"]
        assert description in prompt_text
    assert "spec_prompts/domain_context/engine_overview.txt" in prompt_text
    assert (
        "spec_prompts/domain_context/types.txt" in prompt_text
        or "spec_prompts/domain_context/module_types/" in prompt_text
    )

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


def test_verify_pre_specced_functions(mock_backend, tmp_path):
    """Stage 6b: verify pre-specced functions."""
    proj = setup_workspace(
        tmp_path,
        include_golden=[
            "source_files.json",
            "modules.json",
            "domain_context",
            "extracted_functions",
        ],
    )
    work_dir = proj / "fm_agent"
    _stage_domain_context(work_dir)
    input_dir = work_dir / "extracted_functions"
    output_dir = work_dir / "logic_verification_results"
    output_dir.mkdir(exist_ok=True)

    from src.domain_knowledge import load_generated_domain_context_text
    from src.verification import streaming_reasoner

    sample_rel = _golden_verified_file_list()[0]
    generated_context = load_generated_domain_context_text(str(work_dir), sample_rel)
    assert "Generated domain context:" in generated_context
    assert "engine_overview.txt" in generated_context

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


def test_validate_mismatches_as_bugs(mock_backend, tmp_path):
    """Stage 6c: validate each MISMATCH as a candidate bug."""
    proj = setup_workspace(
        tmp_path,
        include_golden=[
            "source_files.json",
            "modules.json",
            "domain_context",
            "extracted_functions",
            "logic_verification_results",
        ],
    )
    work_dir = proj / "fm_agent"
    _stage_domain_context(work_dir)

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
    validation_events = [
        event for event in mock_backend.events
        if event["stage"] == "bug_validation"
    ]
    assert validation_events
    for event in validation_events:
        assert "fm_agent/spec_prompts/domain_context/engine_overview.txt" in event["input_files"]
        assert (
            "fm_agent/spec_prompts/domain_context/types.txt" in event["input_files"]
            or any(
                path.startswith("fm_agent/spec_prompts/domain_context/module_types/")
                for path in event["input_files"]
            )
        )


def _assert_verification_schema(results_dir: Path):
    for path in iter_json_files(results_dir):
        data = load_json(path)
        assert isinstance(data.get("function"), str)
        assert data.get("verdict") in {"MATCH", "MISMATCH", "ERROR"}
        if data["verdict"] == "MISMATCH":
            assert isinstance(data.get("gaps"), dict)
        if data["verdict"] == "ERROR":
            assert isinstance(data.get("error"), str)
