project_root := justfile_directory()

test:
    PYTHONPATH={{project_root}}/src python3 -m pytest -q {{project_root}}/tests

doctor *args:
    PYTHONPATH={{project_root}}/src python3 -m bip375_interop.cli --config {{project_root}}/interop.yaml {{args}} doctor

validate scenario:
    PYTHONPATH={{project_root}}/src python3 -m bip375_interop.cli --config {{project_root}}/interop.yaml validate {{scenario}}


# Release gate: both validators on every bip375 scenario, plus both MuSig2 regtest legs.
release *args:
    PYTHONPATH={{project_root}}/src python3 -m bip375_interop.cli --config {{project_root}}/interop.yaml {{args}} check --release

# One MuSig2-SP leg on a throwaway regtest node: architecture is
# aggregate-then-derive or derive-then-aggregate. See scripts/musig2-regtest.sh for env vars.
musig2-regtest architecture="aggregate-then-derive":
    {{project_root}}/scripts/musig2-regtest.sh {{architecture}}

# Regenerate the stored initial PSBT that `check` binds to this architecture's scenario.
musig2-psbt architecture="aggregate-then-derive":
    INITIAL_ONLY=1 {{project_root}}/scripts/musig2-regtest.sh {{architecture}}
