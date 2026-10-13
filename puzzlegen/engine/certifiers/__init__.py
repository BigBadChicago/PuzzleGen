"""The engine's own validators, one per game, written from the rules.

Each module here re-derives its game's rules from the published payload and
the stored solution. None of them may import anything from the game whose
boards they judge -- not its verifier, not its deduction helpers, not its
descriptor -- because a validator that called the game's own code would agree
with it by construction and check nothing. The rule is enforced by
``TestCertifierIndependence`` in the invariant suite, not by convention.

What a module here may import: the standard library and
:mod:`puzzlegen.engine.solvers`, whose search shapes and honest completeness
reporting are the engine's own.
"""
