# Data model

Authoritative fields, relations, constraints, and offsets live in [contracts/demo-data.md](../../contracts/demo-data.md). Code mirrors that contract in `demo_data/models.py`.

Generate records, render documents and annotations from those records, then validate and export. Stable IDs and timestamps do not depend on wall-clock time. SQLite is a materialized inspection snapshot of the same generated models, not another hand-maintained source.
