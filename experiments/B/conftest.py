"""Keep the workload fixture out of the repository's own test run.

``fixture/`` is the *starting state* of the migration the agent is asked to perform, so its
contract tests are meant to fail until an agent (or ``reference_solution.py``) fixes them.
They are executed inside the session sandbox by ``fixture/acme_billing/grade.py``, never by
``pytest -c runtime/pyproject.toml``.
"""

collect_ignore_glob = ["fixture/*"]
