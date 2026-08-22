"""`app.contract` is a BINDING to the shared contract, not a second copy of it.

`tests/test_contract.py` tests the contract's BEHAVIOUR and is untouched: it
passed against the fork and it passes against the binding, which is the only
evidence that mattered. What this file adds is what behaviour cannot show: that
the fork is *gone* (not duplicated), that nothing already coined moved, that the
Italian sentences did not drift now that they are a value passed to somebody
else's function — and one claim the binding accidentally weakened over in
`test_adapters.py`, re-made here in a way a binding cannot weaken.

Why each one exists:

* **provenance.** A subclass would satisfy every behavioural test while being a
  fork that had learnt to look like a binding. `is` is the assertion that cannot
  be satisfied by a copy;
* **id stability.** The core mints under its own namespace and the chatbot mints
  under `TOOL_NAMESPACE`; passing the wrong one changes nothing visible today and
  silently re-mints every id this assistant has ever produced. So the expected
  values are HARDCODED here — computed once from the fork, before it was
  replaced (2026-08-22) — because an id test that recomputes the id it asserts
  measures nothing.
"""

from __future__ import annotations

import pathlib

import s3dgraphy.contract.core as core

from app import contract
from app.contract import (GraphDelta, Slot, ToolDescriptor, ToolHandler,
                          ToolRegistry, ToolResult, UNKNOWN_INTENT, invoke,
                          stable_id)


# ── the fork is gone, not duplicated ────────────────────────────────────────

def test_the_chatbot_names_are_the_shared_core_s_objects():
    assert ToolDescriptor is core.Descriptor
    assert ToolRegistry is core.Registry
    assert ToolResult is core.Result
    assert GraphDelta is core.Delta
    assert ToolHandler is core.Handler
    assert Slot is core.Slot
    # …and the invocation is the shared one, wearing this consumer's sentences
    assert contract.invoke is not core.invoke, "the wrapper carries the words"
    assert contract.REFUSALS.unknown == UNKNOWN_INTENT


def test_nothing_is_defined_here_any_more():
    """The invariant, read off the file. A dataclass or a Registry body in this
    module would mean the fork came back — and it would come back the way forks
    do: one class at a time, each with a good local reason."""
    source = pathlib.Path(contract.__file__).read_text(encoding="utf-8")
    assert "@dataclass" not in source
    assert "class ToolRegistry" not in source
    assert "class ToolDescriptor" not in source
    # the only `def invoke` is the wrapper, and it delegates
    assert source.count("def invoke") == 1
    assert "_core_invoke(" in source


# ── nothing already coined moved ────────────────────────────────────────────

def test_the_ids_are_the_ones_this_assistant_has_always_minted():
    assert str(contract.TOOL_NAMESPACE) == "3011d547-e336-535b-825d-19fe499b70fa"
    assert stable_id("create_su", "US", "12") == \
        "2de2f79a-2de8-5210-a1d3-701c3a532140"
    assert stable_id("photo", "abc") == "24595616-62ee-5bdc-a086-602e5a7e68d7"
    # …and it is NOT the core's namespace, which is the mistake this pins against
    assert contract.TOOL_NAMESPACE != core.CONTRACT_NAMESPACE
    assert stable_id("create_su", "US", "12") != \
        core.stable_id("create_su", "US", "12")


# ── the sentences are still Italian, and byte-identical ─────────────────────

def test_the_four_refusals_are_word_for_word_what_they_were():
    """The wording is asserted in `test_contract.py` too, through behaviour. Here
    it is asserted as TEXT, because the sentences are now a value passed to
    somebody else's function — the place a translation could quietly drift."""
    registry = ToolRegistry()
    registry.register(ToolDescriptor(name="create_su", intents=["nuova us"],
                                     input_schema=[Slot("us")],
                                     handler=lambda s, a: ToolResult(True, "ok")))
    assert invoke(None, {}, None, registry=registry).message == \
        "Non so fare questa cosa. So fare: create_su."
    assert invoke(registry.route("nuova us"), {}, "0000-0001").message == \
        "Mi manca us."

    declared = registry.register(ToolDescriptor(name="atrium",
                                                intents=["atrium"]))
    assert invoke(declared, {}, "0000-0001").message == \
        "Lo strumento «atrium» è dichiarato ma non ancora collegato a un servizio."

    writer = ToolDescriptor(name="scrivi", intents=["scrivi"],
                            handler=lambda s, a: ToolResult(True, "fatto"))
    assert invoke(writer, {}, None).message == \
        "Non posso scrivere senza sapere chi sei: serve un'identità verificata."

    def boom(slots, author):
        raise RuntimeError("il servizio non risponde")
    broken = ToolDescriptor(name="atrium2", intents=["atrium2"], handler=boom)
    assert invoke(broken, {}, "0000-0001").message == \
        "«atrium2» non è riuscito: il servizio non risponde"


# ── the reach the binding accidentally shrank ───────────────────────────────

def test_the_adapters_still_reach_only_the_declared_surface():
    """Recovers a claim the binding silently weakened, WITHOUT touching the test
    that made it.

    `test_adapters.py::test_the_adapters_use_ONLY_the_contracts_public_surface`
    identifies what an adapter took from the contract by `value.__module__`. That
    worked while the dataclasses were DEFINED here; now they are the core's, so
    their `__module__` is `s3dgraphy.contract.core` and the same test sees one
    name (`invoke`) where it used to see six. It still passes — and it still
    forbids a private name — but its reach shrank, which is exactly the sort of
    thing that goes unnoticed because nothing turns red.

    So the same claim, measured by IDENTITY against what this module exports,
    which a binding cannot weaken. (The oracle stays untouched; the one-line fix
    over there, when somebody wants it, is to compare against `contract.__all__`
    instead of `__module__`.)
    """
    from app.adapters import atrium, pyarchinit

    public = set(contract.__all__)
    for module in (atrium, pyarchinit):
        took = {name for name in vars(module)
                if name in public and vars(module)[name] is getattr(contract, name)}
        assert {"Slot", "ToolDescriptor", "ToolRegistry", "ToolResult"} <= took, \
            f"{module.__name__} took {sorted(took)}"
        # …and nothing the binding keeps to itself: reaching for `_core_invoke`
        # would be an adapter going around the sentences
        for hidden in ("_core_invoke", "_core_stable_id"):
            assert hidden not in vars(module), \
                f"{module.__name__} reaches past the binding for {hidden}"


def test_the_data_key_this_assistant_has_always_used_is_still_there():
    """`/say` hands `data` verbatim to a device somebody is holding. The core
    files the acting op under `op`; this consumer has always called it `tool`, so
    the binding carries both rather than making somebody find every client."""
    registry = ToolRegistry()
    tool = registry.register(ToolDescriptor(
        name="create_su", intents=["nuova us"], input_schema=[Slot("us")],
        handler=lambda s, a: ToolResult(True, "fatta", data={"us": s["us"]})))
    done = invoke(tool, {"us": "12"}, "0000-0001")
    assert done.data["tool"] == "create_su" == done.data["op"]
    refused = invoke(tool, {}, "0000-0001")
    assert refused.data["tool"] == "create_su"
    assert refused.data["missing"] == ["us"]
