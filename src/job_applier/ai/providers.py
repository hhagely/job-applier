"""AI CLI provider registry + sandboxed invocation.

The security contract (Finding 5 in the plan): job descriptions are third-party
scraped text and are fed to a CLI that *can* run tools. So every invocation is
sandboxed — tools disabled, run in a throwaway cwd with no repo files reachable,
a scrubbed env, argv only (never ``shell=True``), and a timeout. Model output is
treated as data.

All provider-specific CLI flags live in ``build_argv`` / ``version_argv`` so flag
drift across CLI versions is a one-line edit here (the single point of drift).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional, TypeVar

from pydantic import BaseModel, ValidationError

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

    def version_argv(self) -> list[str]:
        return [self.bin, "--version"]

    def build_argv(
        self, prompt: str, *, expect_json: bool = False, model: Optional[str] = None
    ) -> list[str]:
        raise NotImplementedError


class ClaudeProvider(Provider):
    name = "claude"
    display_name = "Claude Code"
    bin = "claude"
    tier = "recommended"

    def build_argv(
        self, prompt: str, *, expect_json: bool = False, model: Optional[str] = None
    ) -> list[str]:
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

    def build_argv(
        self, prompt: str, *, expect_json: bool = False, model: Optional[str] = None
    ) -> list[str]:
        # Non-interactive prompt mode; no tool grants are given.
        argv = [self.bin, "-p", prompt]
        if model:
            argv += ["-m", model]
        return argv


class CodexProvider(Provider):
    name = "codex"
    display_name = "Codex CLI"
    bin = "codex"
    tier = "best-effort"

    def build_argv(
        self, prompt: str, *, expect_json: bool = False, model: Optional[str] = None
    ) -> list[str]:
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

    def build_argv(
        self, prompt: str, *, expect_json: bool = False, model: Optional[str] = None
    ) -> list[str]:
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
    expect_json: bool = False,
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

    argv = provider.build_argv(prompt, expect_json=expect_json, model=model)
    env = _scrubbed_env()

    # A temp cwd means no repo files are reachable even if a tool sneaks through.
    tmp: Optional[tempfile.TemporaryDirectory] = None
    if cwd is None:
        tmp = tempfile.TemporaryDirectory(prefix="job-applier-ai-")
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
