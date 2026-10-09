"""Runner — the trusted executor (AD-1).

The runner is the only process that touches a clone, a build, or a running
instance. Nothing here is reachable over HTTP; it polls the store for commands
and executes the fixed stage pipeline:

    clone → detect → build → run → probe → advertise

Start it with `python -m runner.main`.
"""