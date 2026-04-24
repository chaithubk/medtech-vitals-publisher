"""CLI entry point for ``python -m src``.

Delegates to :func:`src.simulator.main` so both
``python -m src`` and ``python -m src.simulator`` behave identically.
"""

from src.simulator import main

main()
