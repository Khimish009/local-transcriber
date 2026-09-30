"""Quality benchmark tooling (TASKS.md Phase 10).

`run` executes the pipeline over a folder of recordings for several ASR models, reusing one
diarization per recording. `report` turns those runs into a readable comparison and holds
no ML imports, so it can run and be unit-tested outside the worker image.
"""
