"""Single-tenant seed constants.

DEFAULT_ATHLETE_ID must match the literal hardcoded in
alembic/versions/96dc975df352_initial_schema.py. Deliberately duplicated rather than the
migration importing this module: migrations are frozen historical records and shouldn't
depend on application code that can change later.
"""

DEFAULT_ATHLETE_ID = "01KZ2P1FN0AWE6ASCDQ9X847WP"
