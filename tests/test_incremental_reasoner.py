import json
from pathlib import Path


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_modules(work_dir: Path, modules):
    _write(
        work_dir / "modules.json",
        json.dumps(
            {
                "project": "incremental",
                "languages": ["C++"],
                "file_extensions": ["cpp"],
                "modules": modules,
            },
            indent=2,
        ),
    )


def test_incremental_scope_keeps_changed_module_when_llm_selects_none(
    tmp_path,
    monkeypatch,
):
    """Git diff source files are merged into module/file scope even if LLM misses."""
    proj = tmp_path / "proj"
    work_dir = proj / "fm_agent"
    extracted = work_dir / "extracted_functions"
    _write(proj / "a.cpp", "int fa() { return 1; }\n")
    _write(proj / "b.cpp", "int fb() { return 2; }\n")
    _write(extracted / "a-cpp" / "fa.cpp", "int fa() { return 1; }\n")
    _write(extracted / "b-cpp" / "fb.cpp", "int fb() { return 2; }\n")
    _write_modules(
        work_dir,
        [
            {"name": "alpha", "description": "Alpha code.", "source_files": ["a.cpp"]},
            {"name": "beta", "description": "Beta code.", "source_files": ["b.cpp"]},
        ],
    )

    import src.incremental_reasoner as inc

    monkeypatch.setattr(inc, "_llm_select_json", lambda *args, **kwargs: [])
    monkeypatch.setattr(inc, "_opencode_select_json", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        inc,
        "rank_functions_in_file",
        lambda **kwargs: [{"name": "fb", "score": 3.0}]
        if kwargs["filepath"] == "b.cpp"
        else [],
    )

    selected = inc.collect_relevent_function_scope(
        str(proj),
        "change beta behavior",
        {str(proj / "b.cpp"): {"added": [], "modified": ["fb"], "removed": []}},
    )

    assert selected == ["b-cpp/fb.cpp"]


def test_incremental_scope_uses_module_index_for_duplicate_names(
    tmp_path,
    monkeypatch,
):
    """Module selection is index-addressed so duplicate names do not both match."""
    proj = tmp_path / "proj"
    work_dir = proj / "fm_agent"
    extracted = work_dir / "extracted_functions"
    _write(proj / "a.cpp", "int fa() { return 1; }\n")
    _write(proj / "b.cpp", "int fb() { return 2; }\n")
    _write(extracted / "a-cpp" / "fa.cpp", "int fa() { return 1; }\n")
    _write(extracted / "b-cpp" / "fb.cpp", "int fb() { return 2; }\n")
    _write_modules(
        work_dir,
        [
            {"name": "core", "description": "First core.", "source_files": ["a.cpp"]},
            {"name": "core", "description": "Second core.", "source_files": ["b.cpp"]},
        ],
    )

    import src.incremental_reasoner as inc

    monkeypatch.setattr(inc, "_llm_select_json", lambda *args, **kwargs: [{"index": 1, "name": "core"}])
    monkeypatch.setattr(inc, "_opencode_select_json", lambda *args, **kwargs: ["b.cpp"])
    monkeypatch.setattr(
        inc,
        "rank_functions_in_file",
        lambda **kwargs: [{"name": "fb", "score": 3.0}]
        if kwargs["filepath"] == "b.cpp"
        else [{"name": "fa", "score": 3.0}],
    )

    selected = inc.collect_relevent_function_scope(str(proj), "change second core", {})

    assert selected == ["b-cpp/fb.cpp"]


def test_incremental_scope_keeps_changed_function_when_rank_selects_other_function(
    tmp_path,
    monkeypatch,
):
    """Changed functions are kept in scope even if heuristic ranking misses them."""
    proj = tmp_path / "proj"
    work_dir = proj / "fm_agent"
    extracted = work_dir / "extracted_functions"
    _write(proj / "b.cpp", "int helper() { return 1; }\nint fb() { return 2; }\n")
    _write(extracted / "b-cpp" / "helper.cpp", "int helper() { return 1; }\n")
    _write(extracted / "b-cpp" / "fb.cpp", "int fb() { return 2; }\n")
    _write_modules(
        work_dir,
        [
            {"name": "beta", "description": "Beta code.", "source_files": ["b.cpp"]},
        ],
    )

    import src.incremental_reasoner as inc

    monkeypatch.setattr(inc, "_llm_select_json", lambda *args, **kwargs: [{"index": 0, "name": "beta"}])
    monkeypatch.setattr(inc, "_opencode_select_json", lambda *args, **kwargs: ["b.cpp"])
    monkeypatch.setattr(
        inc,
        "rank_functions_in_file",
        lambda **kwargs: [{"name": "helper", "score": 5.0}],
    )

    selected = inc.collect_relevent_function_scope(
        str(proj),
        "change beta behavior",
        {str(proj / "b.cpp"): {"added": [], "modified": ["fb"], "removed": []}},
    )

    assert selected == ["b-cpp/helper.cpp", "b-cpp/fb.cpp"]


def test_project_call_graph_spans_modules(tmp_path):
    """The incremental project graph is global over all modules, not module-local."""
    work_dir = tmp_path / "fm_agent"
    extracted = work_dir / "extracted_functions"
    _write(extracted / "a-cpp" / "a.cpp", "int a() { return b(); }\n")
    _write(extracted / "b-cpp" / "b.cpp", "int b() { return 0; }\n")
    _write_modules(
        work_dir,
        [
            {"name": "alpha", "description": "Alpha code.", "source_files": ["a.cpp"]},
            {"name": "beta", "description": "Beta code.", "source_files": ["b.cpp"]},
        ],
    )

    from src.incremental_reasoner import _project_call_graph

    callees_map, callers_map, file_map, _edge_aliases = _project_call_graph(str(work_dir))

    assert set(file_map) == {"a-cpp::a", "b-cpp::b"}
    assert "b-cpp::b" in callees_map["a-cpp::a"]
    assert "a-cpp::a" in callers_map["b-cpp::b"]


def test_incremental_spec_update_generates_spec_for_added_function(
    tmp_path,
    monkeypatch,
):
    """An added function with no existing .spec.json gets sidecars generated."""
    proj = tmp_path / "proj"
    work_dir = proj / "fm_agent"
    extracted = work_dir / "extracted_functions"
    func_path = extracted / "new-cpp" / "fresh.cpp"
    _write(func_path, "int fresh() { return 7; }\n")

    import src.incremental_reasoner as inc

    fqn = "new-cpp::fresh"
    new_spec = {
        "signature": "int fresh()",
        "pre_condition": "The function is called with valid arguments.",
        "post_condition": "Returns the fresh value.",
    }
    monkeypatch.setattr(
        inc,
        "_project_call_graph",
        lambda *args, **kwargs: ({fqn: set()}, {fqn: set()}, {fqn: str(func_path)}, {}),
    )
    monkeypatch.setattr(inc, "_topdown_ordered_fqns", lambda *args, **kwargs: [fqn])
    monkeypatch.setattr(
        inc,
        "_modified_function_targets",
        lambda *args, **kwargs: {fqn: str(func_path)},
    )
    monkeypatch.setattr(
        inc,
        "_opencode_generate_spec",
        lambda *args, **kwargs: {
            "spec_updated": True,
            "new_spec": new_spec,
            "info_updated": False,
            "new_info": None,
            "updated_callees": [],
        },
    )

    updated = inc._update_specs_for_intent(
        str(proj),
        str(work_dir),
        "add fresh behavior",
        {str(proj / "new.cpp"): {"added": ["fresh"], "modified": [], "removed": []}},
        [],
    )

    assert updated == ["new-cpp/fresh.cpp"]
    # Source is untouched; the generated spec/info live in the metadata sidecars.
    assert func_path.read_text(encoding="utf-8") == "int fresh() { return 7; }\n"
    spec = json.loads(Path(f"{func_path}.spec.json").read_text(encoding="utf-8"))
    info = json.loads(Path(f"{func_path}.info.json").read_text(encoding="utf-8"))
    assert spec == new_spec
    assert info == {"callees": []}

    from src.file_utils import is_file_ready

    assert is_file_ready(func_path)
