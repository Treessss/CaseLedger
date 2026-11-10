# Archived Alembic revisions

These five revisions were the original migration chain (head `67a80026b550`).
They only covered a few early schema tweaks, while most tables
(`orders`, `products`, `payments`, `accounts`, `recharges`, `consumptions`,
`order_costs`, ...) were actually created by `db.create_all()`, so running
`flask db upgrade` on an empty database produced an incomplete schema.

They were replaced by a single baseline revision
`4d6b0a38c85a_baseline_full_schema_multi_shop_multi_.py`, which creates the
complete current schema (17 tables) in one step. The files are kept here for
reference only; Alembic does not scan this folder.

For a database that was previously stamped with the old revision id, run:

    flask db stamp head
