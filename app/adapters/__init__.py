"""Partner adapters — the proof that the base works from OUTSIDE.

Each adapter is what the design note (§10) promises a partner has to write: **a
descriptor and a thin adapter**, using only the contract's public surface. None
of them imports anything private, none of them changes `contract.py`, and
`tests/test_adapters.py` asserts exactly that — because "pluggable" is a claim,
and a claim that is not measured is a hope.

What each one converts is a **shape of data somebody else already produces**:

* `atrium` — ARC's ATRIUM context sheets (recorded by voice, exported as CSV);
* `pyarchinit` — a PyArchInit US record (REST/SQLite, ~20 forms).

They meet in the same place everything else does: `create_su` and
`attach_photo_to_su`, through `invoke`, with a DTC-attributed delta. The
convergence is on the graph, not between the tools.
"""
