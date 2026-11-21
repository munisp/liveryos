"""livery_core — LiveryOS custom operational core (Frappe app).

Report reference: Ch. 3 (The Custom Operational Core). This app carries the
~30% of the GroundAlliance surface that no ERPNext module or OSS project
covers: the rate engine, dispatch-board server logic, affiliate engine, and
the GA-shaped anti-corruption adapter seam (Ch. 7 §7.4.2).

Design rule: domain logic is framework-free pure Python (testable with plain
pytest, no bench required). Frappe DocType/event wiring lives in thin
adapter modules and must contain no business rules.
"""

__version__ = "0.1.0"
