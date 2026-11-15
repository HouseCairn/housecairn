"""The writers of the guard of the change log (tests/test_sync_guard.py), one module per app.

Each module gives `SCENARIOS`, (model label, kind) -> a write the application runs, and
`NOT_APPLICABLE`, (model label, kind) -> why no write path of that kind exists. A scenario sets
its rows up straight in the tables, which records nothing, then calls the one write under test:
the guard sees a change of the entity only if that write recorded it.
"""
