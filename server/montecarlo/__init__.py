"""Seeded Monte Carlo (input-uncertainty simulation) outside the engine.

The engine stays deterministic and has no RNG. This package draws N input
sets from stated ranges with a private ``random.Random(seed)`` and runs each
draw through the ordinary engine entry point (rev 1, or rev 2 with profile
c, d or e).
"""
LABEL = ("Input-uncertainty simulation (seeded). Modelled, conditional on the "
         "stated input ranges; not a forecast.")
SCHEMA = "supermap.montecarlo/1"
