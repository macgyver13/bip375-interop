project_root := justfile_directory()

test:
    PYTHONPATH={{project_root}}/src python3 -m pytest -q {{project_root}}/tests

doctor *args:
    PYTHONPATH={{project_root}}/src python3 -m bip375_interop.cli --config {{project_root}}/interop.yaml {{args}} doctor

validate scenario:
    PYTHONPATH={{project_root}}/src python3 -m bip375_interop.cli --config {{project_root}}/interop.yaml validate {{scenario}}
