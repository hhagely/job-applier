"""AI CLI provider registry + sandboxed invocation.

The security contract: job descriptions are third-party scraped text and are fed
to a CLI that *can* run tools. Two backstops apply to EVERY provider regardless of
its flags — a throwaway cwd with no repo files reachable, and a scrubbed env (keys
stripped) — plus argv only (never ``shell=True``) and a timeout. On top of those,
the Claude and Codex adapters pass explicit tool/approval-sandbox flags in
``build_argv``; Gemini pins ``--approval-mode default`` + ``-e none`` (the safe
non-interactive posture, which drops confirmation-requiring tools, plus zero
extensions); Ollama has no tools to sandbox and leans on the two shared backstops
alone. Model output is always treated as data.

All provider-specific CLI flags live in ``build_argv`` / ``version_argv`` so flag
drift across CLI versions is a one-line edit here (the single point of drift).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Optional, TypeVar

from pydantic import BaseModel, ValidationError

# On Windows, a console-subsystem child (an AI CLI, a `--version` probe) spawned by
# the windowless packaged backend gets allocated its OWN console window — a command
# prompt that flashes open and shut in the desktop app. CREATE_NO_WINDOW suppresses
# it. Evaluated lazily so the Windows-only flag is never touched off-Windows (0 is a
# no-op creationflags value everywhere else).
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ---- errors ---------------------------------------------------------------


class ProviderError(RuntimeError):
    """Base for provider invocation failures (carries stderr / a message)."""


class ProviderNotFound(ProviderError):
    """Selected provider isn't registered or its binary isn't on PATH."""


class ProviderTimeout(ProviderError):
    """The CLI didn't finish within the timeout."""


class ProviderJSONError(ProviderError):
    """Provider output couldn't be parsed/validated into the expected JSON model."""


_ModelT = TypeVar("_ModelT", bound=BaseModel)


# ---- provider registry ----------------------------------------------------

Tier = str  # "recommended" | "best-effort"

DEFAULT_OLLAMA_MODEL = "llama3.1"


class Provider:
    """A detectable AI CLI. Subclasses own the exact (sandboxed) argv."""

    name: str
    display_name: str
    bin: str
    tier: Tier
    # A lighter/cheaper model tier for high-volume *baseline* scoring (the flow
    # that scores a whole ingest at once). Baseline scoring is triage, not the
    # final gate, so it shouldn't burn the user's subscription window on the top
    # tier. ``None`` means "no cheaper default known for this CLI" -> callers fall
    # back to the configured generation model / account default. Overridable per
    # provider in Settings (persisted as ``ai_scoring_model``).
    scoring_model: Optional[str] = None

    def version_argv(self) -> list[str]:
        return [self.bin, "--version"]

    def build_argv(self, prompt: str, *, model: Optional[str] = None) -> list[str]:
        raise NotImplementedError


class ClaudeProvider(Provider):
    name = "claude"
    display_name = "Claude Code"
    bin = "claude"
    tier = "recommended"
    # Sonnet handles the scoring rubric (5 weighted buckets + hard rules) reliably
    # at a fraction of Opus's usage-window cost. Claude Code accepts the bare alias.
    scoring_model = "sonnet"

    def build_argv(self, prompt: str, *, model: Optional[str] = None) -> list[str]:
        # Sandbox: empty tool allowlist + `dontAsk` permission mode => the CLI
        # auto-denies every tool call, so it cannot edit files or run commands even
        # if the (untrusted) prompt asks it to. NOT `plan` mode: plan tells the model
        # to *propose a plan* instead of answering, so Opus burns minutes "planning"
        # and emits a plan stub ("plan recorded...") rather than the draft JSON —
        # which then fails to parse and retries, blowing the timeout.
        #
        # `--strict-mcp-config` + an empty `--mcp-config` load zero MCP servers.
        # Without it every cold subprocess connects to *all* configured MCP servers
        # (the empty --allowed-tools blocks tool use, not server startup), so an
        # auth-required remote connector stalls the call for minutes. (`--bare` also
        # skips MCP but strips the OAuth login too — "Not logged in" — so we don't.)
        #
        # `--output-format text` returns the model's raw text (not a JSON envelope).
        argv = [
            self.bin,
            "-p",
            prompt,
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--allowed-tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--output-format",
            "text",
        ]
        # Honor the Settings-selected model (e.g. a faster tier for drafting); the
        # CLI's account default is used when unset.
        if model:
            argv += ["--model", model]
        return argv


class GeminiProvider(Provider):
    name = "gemini"
    display_name = "Gemini CLI"
    bin = "gemini"
    tier = "recommended"
    # Flash is Gemini's fast/cheap tier — the Sonnet-equivalent for triage scoring.
    scoring_model = "gemini-2.5-flash"

    def build_argv(self, prompt: str, *, model: Optional[str] = None) -> list[str]:
        # Non-interactive prompt mode, hardened against a prompt-injected job
        # description coaxing the CLI into running a tool:
        #   --approval-mode default : pins the safe posture. In non-interactive (-p)
        #     mode the Gemini CLI excludes confirmation-requiring tools (shell, edit)
        #     from the tool registry unless they're explicitly allow-listed, which we
        #     never do. Passing it explicitly means a later change to the CLI's default
        #     can't silently arm those tools.
        #   -e none : load ZERO extensions, so an extension (or extension-provided MCP
        #     tool) can't be invoked - the Gemini analogue of Claude's empty
        #     --mcp-config.
        # Still backed by the shared throwaway-cwd + scrubbed-env backstops (read-only
        # built-in tools may remain registered; the cwd/env bound their blast radius.
        # A user wanting zero tools can add a .gemini/settings.json with tools.core: []).
        argv = [self.bin, "-p", prompt, "--approval-mode", "default", "-e", "none"]
        if model:
            argv += ["-m", model]
        return argv


class CodexProvider(Provider):
    name = "codex"
    display_name = "Codex CLI"
    bin = "codex"
    tier = "best-effort"

    def build_argv(self, prompt: str, *, model: Optional[str] = None) -> list[str]:
        # Non-interactive exec with a read-only sandbox and no approval prompts.
        return [
            self.bin,
            "exec",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            prompt,
        ]


class OllamaProvider(Provider):
    name = "ollama"
    display_name = "Ollama (local)"
    bin = "ollama"
    tier = "best-effort"

    def build_argv(self, prompt: str, *, model: Optional[str] = None) -> list[str]:
        # Fully local; ollama has no tool/file access to sandbox. Needs a model.
        return [self.bin, "run", model or DEFAULT_OLLAMA_MODEL, prompt]


# Registration order is the display order in the UI.
_PROVIDER_LIST: list[Provider] = [
    ClaudeProvider(),
    GeminiProvider(),
    CodexProvider(),
    OllamaProvider(),
]
PROVIDERS: dict[str, Provider] = {p.name: p for p in _PROVIDER_LIST}


def default_scoring_model(name: str) -> Optional[str]:
    """The provider's built-in lighter tier for baseline (bulk) scoring, or ``None``
    when the CLI has no cheaper default we can name (e.g. Codex, local Ollama)."""
    provider = PROVIDERS.get(name)
    return provider.scoring_model if provider is not None else None


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    display_name: str
    tier: Tier
    available: bool
    version: Optional[str] = None


# ---- detection ------------------------------------------------------------


def _probe_version(provider: Provider, *, timeout: float = 5.0) -> Optional[str]:
    """Run the CLI's ``--version`` to confirm it actually executes. Returns the
    reported version string, or ``None`` if it can't be run."""
    try:
        proc = subprocess.run(
            provider.version_argv(),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_scrubbed_env(),
            check=False,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0].strip() if out else ""


def detect_one(provider: Provider) -> ProviderInfo:
    available = shutil.which(provider.bin) is not None
    version = _probe_version(provider) if available else None
    # A binary on PATH that fails to run --version isn't usable.
    if available and version is None:
        available = False
    return ProviderInfo(
        name=provider.name,
        display_name=provider.display_name,
        tier=provider.tier,
        available=available,
        version=version,
    )


def detect_all() -> list[ProviderInfo]:
    return [detect_one(p) for p in _PROVIDER_LIST]


# ---- sandboxed invocation -------------------------------------------------

# Env vars an AI CLI has no business receiving. Removed from the child env so a
# compromised/injected prompt can't exfiltrate them. The CLI still authenticates
# via its own on-disk config (e.g. ~/.claude), which env scrubbing doesn't touch.
_SENSITIVE_EXACT = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "JOB_APPLIER_API_BASE",
}
_SENSITIVE_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD")
_SENSITIVE_SUBSTR = ("SECRET", "PASSWORD", "PRIVATE_KEY")


def _scrubbed_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        up = k.upper()
        if up in _SENSITIVE_EXACT:
            continue
        if up.endswith(_SENSITIVE_SUFFIXES):
            continue
        if any(sub in up for sub in _SENSITIVE_SUBSTR):
            continue
        env[k] = v
    return env


def run(
    name: str,
    prompt: str,
    *,
    timeout: float = 120,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Invoke provider ``name`` on ``prompt`` and return its stdout text.

    Sandboxed: argv only (no shell), scrubbed env, throwaway cwd. Raises
    ``ProviderNotFound`` / ``ProviderTimeout`` / ``ProviderError``.
    """
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ProviderNotFound(f"unknown provider: {name}")
    if shutil.which(provider.bin) is None:
        raise ProviderNotFound(f"'{provider.bin}' is not installed / not on PATH")

    # The prompt is passed as an argv string; NUL bytes are illegal in a process
    # argument (Windows raises ValueError "embedded null character") and carry no
    # meaning for the model. Prompts are assembled from PDF-extracted resume text
    # and scraped job descriptions, both of which routinely contain stray NULs, so
    # strip them here — the single choke point for every provider flow.
    prompt = prompt.replace("\x00", "")

    argv = provider.build_argv(prompt, model=model)
    env = _scrubbed_env()

    # A temp cwd means no repo files are reachable even if a tool sneaks through.
    # ignore_cleanup_errors: on Windows a CLI (or an AV scanner) can still hold a
    # handle in the dir when cleanup() runs in the finally below, and an
    # uncaught PermissionError there would escape as an opaque 500. A leaked temp
    # dir is harmless (the OS reclaims it); a failed AI call is not.
    tmp: Optional[tempfile.TemporaryDirectory] = None
    if cwd is None:
        tmp = tempfile.TemporaryDirectory(
            prefix="job-applier-ai-", ignore_cleanup_errors=True
        )
        cwd = tmp.name
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            # The prompt goes in via argv, so the CLI needs no stdin. Without this
            # the child inherits the backend's (non-TTY) stdin and blocks a few
            # seconds waiting for piped input before proceeding ("no stdin data
            # received in 3s"). DEVNULL gives an immediate EOF.
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=env,
            cwd=cwd,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderTimeout(f"{provider.bin} timed out after {timeout}s") from exc
    except OSError as exc:
        raise ProviderError(f"failed to run {provider.bin}: {exc}") from exc
    finally:
        if tmp is not None:
            tmp.cleanup()

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
        raise ProviderError(stderr)
    return (proc.stdout or "").strip()


# ---- tolerant JSON extraction (used by later scoring/drafting phases) ------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of possibly-chatty model output.

    Strips ``` fences, then scans for the first balanced ``{...}`` and parses it.
    Raises ``ValueError`` when no valid JSON object is found.
    """
    import json

    if not text or not text.strip():
        raise ValueError("empty model output")

    candidates: list[str] = []
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(text)

    for chunk in candidates:
        obj = _first_balanced_object(chunk)
        if obj is not None:
            try:
                parsed = json.loads(obj)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("no JSON object found in model output")


def _first_balanced_object(text: str) -> Optional[str]:
    """Return the first brace-balanced ``{...}`` substring, honoring strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ---- run + validate JSON --------------------------------------------------


def run_json(
    name: str,
    prompt: str,
    schema: type[_ModelT],
    *,
    model: Optional[str] = None,
    timeout: float = 120,
    nudge: str = "IMPORTANT: return ONLY the JSON object, no prose or fences.",
    attempts: int = 2,
) -> _ModelT:
    """Run provider ``name`` and validate its output into ``schema``.

    Retries once (up to ``attempts``) with ``nudge`` appended when the first
    response doesn't parse. This "run -> extract_json -> model_validate, retry
    with a nudge" loop was copy-pasted into the scoring, drafting, and suggest
    flows; it lives here once. Provider invocation errors (timeout, not found,
    non-zero exit) propagate as ``ProviderError`` subclasses; a parse/validation
    failure after the last attempt is raised as ``ProviderJSONError`` for the
    caller to map to its flow-specific error.
    """
    last_err: Optional[Exception] = None
    for attempt in range(attempts):
        text = prompt if attempt == 0 else f"{prompt}\n\n{nudge}"
        raw = run(name, text, model=model, timeout=timeout)
        try:
            return schema.model_validate(extract_json(raw))
        except (ValueError, ValidationError) as exc:
            last_err = exc
    raise ProviderJSONError(str(last_err))
