# reportkit conventions

- Standard library only; do not add third-party dependencies.
- Run the checks with `python3 -m pytest experiments/J/fixture/tests -q`.
- Every public function keeps explicit type annotations.
- Every behaviour change lands with a test that covers it.
- Never change a test's expectation to make it pass.
