"""API package — HTTP control plane.

The api has zero container-runtime dependencies (AD-1). It owns identity, the
Deploy form, the dashboard, and live-log streaming; it emits commands into the
store and reads projections. The runner is the only executor.
"""