# Data model

Source shapes remain in [contracts/demo-data.md](../../contracts/demo-data.md). PostgreSQL column types and constraints are implemented in `demo_data/schema.sql` and mapped in that contract. Documents retain complete exact source text; document/payment references become join-table rows. Evaluation entities stay file-only.

Load states: validated files -> schema ready inside transaction -> source inserts -> verified readback -> commit. Failures roll back schema and source mutations. An already identical namespace is accepted without reinsertion; a different populated namespace is rejected.
