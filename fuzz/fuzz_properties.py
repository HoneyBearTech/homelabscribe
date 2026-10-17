"""Coverage-guided fuzzing (Atheris) of the Hypothesis properties in tests/test_fuzz_parsers.py.

libFuzzer feeds each property's `.hypothesis.fuzz_one_input`, so a fuzzer finding is a failing property.

Atheris only has Linux x86_64 wheels, so this runs in CI (.github/workflows/fuzz.yml) or an amd64 container:

    pip install --require-hashes -r requirements/fuzz.txt
    python fuzz/fuzz_properties.py <target> [libFuzzer flags]      # e.g. -max_total_time=60
    python fuzz/fuzz_properties.py <target> <crash-file>           # replay a finding
"""

import sys
from pathlib import Path

import atheris
from hypothesis import settings

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

# Registered before the properties are imported so their settings inherit it: the fuzzer owns the inputs
# and the time budget, so there is no example database and no deadline.
settings.register_profile("fuzz", deadline=None, database=None)
settings.load_profile("fuzz")

TARGETS = {
    "env_names": "test_env_names_never_contains_values",
    "clean_labels": "test_clean_labels_drops_htpasswd_hashes",
    "depends_on": "test_parse_depends_on_returns_sorted_unique",
    "ports": "test_parse_ports_accepts_docker_shaped_input",
    "container": "test_parse_container_only_fails_with_expected_errors",
    "container_secrets": "test_parse_container_never_stores_env_values",
    "guest": "test_parse_guest_only_fails_with_expected_errors",
    "notes": "test_notes_merge_keeps_markers_and_notes",
    "license": "test_license_verify_rejects_garbage_cleanly",
}


def main() -> None:
    """Fuzz the target named on the command line; libFuzzer takes the remaining arguments."""
    if sys.argv[1:2] == [] or sys.argv[1] not in TARGETS:
        sys.exit(f"usage: {sys.argv[0]} <target> [libFuzzer flags]\ntargets: {', '.join(TARGETS)}")
    target = sys.argv.pop(1)

    # PyYAML and pydantic are guidance too: with only homelabscribe instrumented, the fuzzer cannot
    # tell inputs apart.
    with atheris.instrument_imports(include=["homelabscribe", "yaml", "pydantic"]):
        import test_fuzz_parsers  # noqa: PLC0415 - imported inside the block so it gets instrumented

    prop = getattr(test_fuzz_parsers, TARGETS[target])
    atheris.Setup(sys.argv, prop.hypothesis.fuzz_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
