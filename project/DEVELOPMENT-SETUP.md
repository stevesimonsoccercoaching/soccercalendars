# Development environment setup

## Calendar processing dependencies

The root requirements.txt defines the tested Python
dependencies for calendar processing.

GitHub Actions and local development environments
must use the same dependency versions.

## WSL

Activate the existing project virtual environment:

    source ~/.venvs/soccercalendars/bin/activate

From the repository root:

    python -m pip install -r requirements.txt
    python -m pip check

## Termux

From the repository root:

    python -m pip install -r requirements.txt
    python -m pip check

## Regression testing

From the repository root:

    python -B -m unittest discover -s tests

## Dependency upgrades

The CJSL builder uses openpyxl implementation details
to distinguish recognized workbook corruption from
unexpected programming failures.

Dependency upgrades must be deliberate and reviewed.

Before accepting an upgrade:

1. Review dependency changes.
2. Verify exception-classification compatibility.
3. Run the complete regression suite.
4. Rehearse publication from a committed candidate.
5. Complete integration validation.

Do not upgrade openpyxl independently of requirements.txt.

Playwright and browser automation dependencies are
outside this calendar processing dependency file.
Their dependency management remains tracked under M01.

See PROJECT-REGISTER.md for outstanding requirements
and acceptance criteria.
