"""Mock backend for FM-Agent integration tests.

The mock backend implements the two external operations that are
expensive/non-deterministic in CI:

1. ``src.opencode_trace.run_opencode_traced`` - the opencode CLI agent calls.
2. ``src.reasoner.reasoner`` - the LLM reasoning inside verification.

The key for each call is derived from the existing call context
(stage / metadata / function_ids / trace_context), so tests exercise the same
pipeline control flow while replaying recorded fixture artifacts.
"""

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from tests.helpers import DEFAULT_SCENARIO, load_scenario


class FixtureBackend:
    """Replay recorded trace outputs from a scenario fixture."""

    def __init__(self, scenario=None):
        if scenario is None:
            scenario = DEFAULT_SCENARIO
        elif isinstance(scenario, (str, Path)):
            path = Path(scenario)
            if path.is_dir():
                scenario = {"golden": path, "backend": DEFAULT_SCENARIO["backend"]}
            else:
                scenario = load_scenario(path)
        self.scenario = scenario
        self.golden = Path(scenario["golden"])
        self.backend_config = scenario["backend"]
        self.events = []

    # ------------------------------------------------------------------ helpers
    def _record_event(
        self,
        *,
        stage,
        function_ids=None,
        input_files=None,
        output_files=None,
        summary=None,
        metadata=None,
    ):
        self.events.append(
            {
                "stage": stage,
                "function_ids": list(function_ids or []),
                "input_files": list(input_files or []),
                "output_files": list(output_files or []),
                "summary": summary,
                "metadata": dict(metadata or {}),
            }
        )

    def _copy_fixture_path(self, src_rel: str, dst_rel: str, work_dir: Path):
        src = self.golden / src_rel
        dst = work_dir / dst_rel
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            return
        if not src.exists():
            raise FileNotFoundError(f"Fixture source does not exist: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def _function_path(self, function_id: str) -> Path:
        """Map ``query_q1-cpp::data`` to the golden extracted-function file."""
        file_slug, func_name = function_id.split("::")
        func_dir = self.golden / "extracted_functions" / file_slug
        if not func_dir.exists():
            raise KeyError(f"No golden extracted_functions dir for {function_id}")
        matches = list(func_dir.glob(f"{func_name}.*"))
        if len(matches) == 1:
            return matches[0]
        # Disambiguate with common extensions.
        for ext in (".cpp", ".hpp", ".c", ".h", ".cc", ".cxx"):
            p = func_dir / f"{func_name}{ext}"
            if p.exists():
                return p
        raise KeyError(f"No golden file for {function_id}")

    def _bug_id(self, function_id: str) -> str:
        """Map ``query_q1-cpp::data`` to ``query_q1-cpp--data``."""
        return function_id.replace("::", "--")

    def _verification_result(self, function_id: str) -> dict:
        file_slug, func_name = function_id.split("::")
        results_root = self.backend_config["reasoner"]["results_root"]
        path = self.golden / results_root / file_slug / f"{func_name}.json"
        if not path.exists():
            raise KeyError(f"No golden verification result for {function_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _materialize_static_outputs(self, action: dict, work_dir: Path):
        for item in action.get("materialize", []):
            self._copy_fixture_path(item["from"], item["to"], work_dir)

    def _materialize_function_outputs(
        self, action: dict, function_ids: list[str], work_dir: Path
    ):
        spec = action.get("materialize_by_function_id")
        if not spec:
            return

        kind = spec.get("kind")
        if kind == "function_file":
            to_root = Path(spec["to_root"])
            for fid in function_ids:
                src = self._function_path(fid)
                dst = work_dir / to_root / src.parent.name / src.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            return

        if kind == "bug_validation":
            if not function_ids:
                raise ValueError("bug_validation stage requires a function_id")
            src_root = self.golden / spec["from_root"]
            dst_root = work_dir / spec["to_root"]
            dst_root.mkdir(parents=True, exist_ok=True)
            for fid in function_ids:
                values = {"function_id": fid, "bug_id": self._bug_id(fid)}
                for file_spec in spec.get("files", []):
                    pattern = file_spec["path"]
                    rel = pattern.format(**values)
                    src = src_root / rel
                    if not src.exists():
                        if file_spec.get("optional"):
                            continue
                        raise FileNotFoundError(f"Fixture source does not exist: {src}")
                    shutil.copy2(src, dst_root / rel)
            return

        raise NotImplementedError(f"Unknown materialize_by_function_id kind={kind!r}")

    def assert_workflow(self):
        """Validate broad workflow expectations declared by the scenario."""
        workflow = self.scenario.get("workflow", {})
        counts = Counter(event["stage"] for event in self.events)

        missing = [
            stage for stage in workflow.get("required_stages", [])
            if counts[stage] == 0
        ]
        assert not missing, f"Expected workflow stage(s) were not called: {missing}"

        for stage, rule in workflow.get("stage_calls", {}).items():
            stage_events = [event for event in self.events if event["stage"] == stage]
            minimum = rule.get("min")
            if minimum is not None:
                assert len(stage_events) >= minimum, (
                    f"Expected at least {minimum} call(s) to {stage}, "
                    f"got {len(stage_events)}"
                )
            for field in rule.get("requires", []):
                for event in stage_events:
                    assert _event_has_field(event, field), (
                        f"Workflow event for {stage} missing required field {field!r}: "
                        f"{event}"
                    )

    # ------------------------------------------------------------------ agents
    def run_opencode_traced(
        self,
        *,
        proj_dir,
        work_dir,
        command,
        stage,
        function_ids=None,
        input_files=None,
        output_files=None,
        summary=None,
        metadata=None,
    ):
        """Mock replacement for ``run_opencode_traced``.

        Uses the *existing* ``stage`` and ``function_ids`` arguments to decide
        which recorded output files to materialise in the workspace.
        """
        function_ids = function_ids or []
        work_dir = Path(work_dir)
        self._record_event(
            stage=stage,
            function_ids=function_ids,
            input_files=input_files,
            output_files=output_files,
            summary=summary,
            metadata=metadata,
        )

        action = self.backend_config["opencode"].get(stage)
        if not action:
            raise NotImplementedError(
                f"Mock backend does not know how to handle stage={stage!r}"
            )
        self._materialize_static_outputs(action, work_dir)
        self._materialize_function_outputs(action, function_ids, work_dir)

        return subprocess.CompletedProcess(command, 0)

    # ------------------------------------------------------------------ reasoner
    def reasoner(self, func, spec, info, language, trace_context=None):
        """Mock replacement for ``reasoner``.

        Returns the verification string that ``_verify_single_file`` expects,
        reconstructed from the recorded golden result JSON.
        """
        trace_context = trace_context or {}
        fid = trace_context.get("function_id")
        if not fid:
            raise ValueError("reasoner mock requires trace_context.function_id")

        result = self._verification_result(fid)
        verdict = result.get("verdict")

        if verdict == "MATCH":
            return (
                "The function passes the verification. "
                "All code blocks satisfy the specification's post-condition."
            )

        if verdict == "MISMATCH":
            gaps = result.get("gaps") or {}
            return (
                "Verification FAILED.\n"
                f"Statements triggering the violation:\n{gaps.get('code_evidence', '')}\n\n"
                f"Post-condition:\n{gaps.get('actual_behavior', '')}\n\n"
                f"Reason for violation:\n{gaps.get('trigger_condition', '')}"
            )

        if verdict == "ERROR":
            error = result.get("error", "Unknown verification error")
            return f"Failed to generate post-condition for block 1. {error}"

        # Fallback for any unexpected verdict.
        return f"Failed to generate post-condition for block 1. (verdict={verdict})"


def _event_has_field(event: dict, dotted: str):
    value = event
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    if isinstance(value, (list, dict, str)):
        return bool(value)
    return value is not None
