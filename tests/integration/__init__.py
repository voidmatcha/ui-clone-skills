# Marker file — makes `tests/integration/conftest.py` resolve as
# `tests.integration.conftest` so mypy can distinguish it from the
# sibling `tests/conftest.py`. Without this, mypy fails with
# "Duplicate module named 'conftest'". pytest doesn't need this.
