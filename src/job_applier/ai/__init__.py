"""Provider-agnostic AI substrate.

Detects installed AI CLIs and invokes the chosen one **sandboxed** (no tools,
no file access, text-in / JSON-out) on the user's own subscription. Never calls
a vendor SDK or handles API keys — same "hand a staged prompt to a CLI that owns
its auth" pattern the project already uses, just invoked by the app. See
``providers.py`` for the registry and the sandboxed ``run()``.
"""
