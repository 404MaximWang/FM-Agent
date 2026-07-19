#!/usr/bin/env python3
"""Build the local replay mock fixture from an existing trace.

Source: FM_AGENT_REPLAY_TRACE
Project source: FM_AGENT_REPLAY_PROJECT_SRC
Destination: tests/fixtures/replay

The fixture directory is ignored by git. The default scenario reads this path,
or FM_AGENT_REPLAY_FIXTURE can point tests at a different fixture directory.
"""

import os
import shutil
from pathlib import Path


def required_path_env(name: str):
    try:
        return Path(os.environ[name])
    except KeyError as exc:
        raise SystemExit(f"Set {name} before generating a replay fixture") from exc


TRACE = required_path_env("FM_AGENT_REPLAY_TRACE")
PROJECT_SRC = required_path_env("FM_AGENT_REPLAY_PROJECT_SRC")
FIXTURE_ROOT = Path(
    os.environ.get("FM_AGENT_REPLAY_FIXTURE", Path(__file__).parent / "replay")
)
FIXTURE = FIXTURE_ROOT / "golden"
FIXTURE_SRC = FIXTURE_ROOT / "src"


def main():
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    FIXTURE.mkdir(parents=True)

    if FIXTURE_SRC.exists():
        shutil.rmtree(FIXTURE_SRC)

    # Phase plan and setup outputs.
    shutil.copy2(TRACE / "phases.json", FIXTURE / "phases.json")

    # Spec generation outputs: extracted functions now contain [SPEC]/[INFO].
    shutil.copytree(TRACE / "extracted_functions", FIXTURE / "extracted_functions")

    # Domain context files produced by setup.
    shutil.copytree(
        TRACE / "spec_prompts" / "domain_context",
        FIXTURE / "domain_context",
    )

    # Verification outputs.
    shutil.copytree(
        TRACE / "logic_verification_results",
        FIXTURE / "logic_verification_results",
    )

    # Bug validation reports (agent-written reports and their result JSON).
    # We skip the generated _probe_*.py scripts because those run the real
    # engine and are not reproducible in a headless CI environment.
    bug_src = TRACE / "bug_validation"
    bug_dst = FIXTURE / "bug_validation"
    bug_dst.mkdir(parents=True)
    for f in bug_src.iterdir():
        if f.suffix == ".md" or f.name.endswith(".result.json"):
            shutil.copy2(f, bug_dst / f.name)

    shutil.copytree(
        PROJECT_SRC,
        FIXTURE_SRC,
        ignore=shutil.ignore_patterns("fm_agent", ".git", ".omo"),
    )

    print(f"Fixture created at {FIXTURE}")
    print(f"Project source copied to {FIXTURE_SRC}")


if __name__ == "__main__":
    main()
