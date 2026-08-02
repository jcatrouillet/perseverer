"""Import + minimal-operation smoke test for our AVX-sensitive native dependencies.

This file is run twice in CI (see .github/workflows/ci.yml): once natively in the `test`
job, and once under `qemu-x86_64 -cpu qemu64` in the `avx-smoke` job. The `qemu64` CPU model
has no AVX/AVX2, matching the DS1019+'s Celeron J3455 (Goldmont, SSE4.2 only) — if any of
numpy/pyarrow/duckdb ship an AVX-only code path that isn't runtime-dispatched, this crashes
with SIGILL under emulation instead of silently working on GitHub's AVX2-capable runners and
then crashing on the NAS at 4am.
"""

import duckdb
import numpy as np
import pyarrow as pa


def test_numpy_basic_op_no_illegal_instruction() -> None:
    arr = np.arange(10_000, dtype=np.float64)
    assert arr.sum() == arr.sum()


def test_pyarrow_basic_op_no_illegal_instruction() -> None:
    table = pa.table({"a": [1, 2, 3]})
    assert table.num_rows == 3


def test_duckdb_basic_op_no_illegal_instruction() -> None:
    con = duckdb.connect(":memory:")
    result = con.execute("select 1 + 1").fetchone()
    assert result == (2,)
