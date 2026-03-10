"""Experiment wizard — analyzes descriptions and generates project scaffolds."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

logger = logging.getLogger(__name__)

ANALYZE_SYSTEM_PROMPT = """\
You are an experiment design assistant. The user will describe an optimization experiment.
The user message includes a detected runtime environment section — use it to make
hardware-appropriate choices (device selection, framework, compute budget, batch sizes).

Return ONLY valid JSON with two keys:
- "inferred": a dict of parameters you can confidently determine from the description
  AND the detected environment, including:
  name, metric_name, metric_direction, editable_files, timeout_seconds,
  and "architecture_guards" — a list of code-enforced constraints to embed in evaluate.py
  (e.g. "verify neural network is called during decision", "cap editable LOC at 500",
  "ban more than 5 hand-written heuristic functions").
- "uncertain": a list of at most 3 items where you need clarification. Each item has:
  - "param": the parameter name
  - "question": a clear question for the user
  - "choices": a list of options, each with "label" and "explanation"

Important rules:
- When the user specifies an algorithmic approach (e.g. "AlphaZero", "genetic algorithm",
  "reinforcement learning"), you MUST infer architecture_guards that prevent the agent
  from bypassing that approach with hand-coded rules.
- If the environment shows Apple Silicon / MPS, do NOT generate CUDA-specific code
  (Flash Attention, torch.compile with CUDA, NCCL). Use PyTorch MPS or MLX instead.
- If the environment shows no GPU, use CPU-appropriate defaults (smaller models, shorter
  training, no mixed precision).
- If critical environment info is missing or ambiguous, include it in "uncertain".
Do not include any text outside the JSON object.
"""

GENERATE_SYSTEM_PROMPT = """\
You are an experiment scaffold generator for the Crucible platform.
The user will provide a description, resolved decisions, and a detected runtime environment.

Return ONLY valid JSON with two keys:
- "files": a dict mapping relative file paths to their string contents.
- "summary": a one-line summary of the generated experiment.
Do not include any text outside the JSON object.

CRITICAL RULES:
1. Every file value MUST contain COMPLETE, REAL, FUNCTIONAL source code.
   NOT placeholders, NOT abbreviations, NOT "[description]", NOT "<written>",
   NOT "..." or "# see above". If the JSON is too large, reduce code complexity
   rather than truncating file contents.
2. Follow the Crucible Reference below EXACTLY for config.yaml schema,
   evaluate.py output format, and program.md structure.
3. Use the detected environment for hardware-appropriate code
   (MPS for Apple Silicon, CUDA for NVIDIA, CPU fallback).
"""


def _load_scaffold_reference() -> str:
    """Load and trim the crucible scaffold reference for the generate prompt.

    Only extracts format-critical sections (config.yaml schema, evaluate.py
    template, program.md structure, common mistakes) to keep the system
    prompt small enough for large code generation responses.
    """
    full_text = ""
    try:
        from importlib.resources import files
        full_text = files("crucible").joinpath("data/scaffold_reference.md").read_text()
    except Exception:
        repo_skill = Path(__file__).resolve().parents[2] / ".claude/skills/crucible-setup/SKILL.md"
        if repo_skill.exists():
            full_text = repo_skill.read_text()

    if not full_text:
        logger.warning("Could not load scaffold reference — wizard may produce incorrect formats")
        return ""

    # Extract only the sections the wizard needs
    return _extract_sections(full_text, [
        "### Step 2:",   # evaluate.py template + output format
        "### Step 4:",   # program.md structure
        "### Step 5:",   # config.yaml schema
        "## Common Mistakes",
    ])


def _extract_sections(text: str, headers: list[str]) -> str:
    """Extract specific markdown sections from text by header prefix."""
    lines = text.split("\n")
    result: list[str] = []
    capturing = False

    for line in lines:
        # Check if this line starts a section we want
        if any(line.startswith(h) for h in headers):
            capturing = True
            result.append(line)
            continue

        # Stop capturing at next section of same or higher level
        if capturing and line.startswith("#"):
            header_level = len(line) - len(line.lstrip("#"))
            current_level = min(
                len(h) - len(h.lstrip("#"))
                for h in headers
                if any(r.startswith(h) for r in result)
            ) if result else 3
            if header_level <= current_level and not any(line.startswith(h) for h in headers):
                capturing = False
                continue

        if capturing:
            result.append(line)

    return "\n".join(result)

def _detect_environment() -> dict:
    """Detect the runtime environment for hardware-aware scaffold generation."""
    import platform
    import shutil
    import subprocess as sp

    env: dict = {}

    # OS and architecture
    env["os"] = platform.system()
    env["arch"] = platform.machine()

    # Python version
    env["python"] = platform.python_version()

    # Apple Silicon detection
    if env["os"] == "Darwin" and env["arch"] == "arm64":
        env["apple_silicon"] = True
        # Check for MLX
        try:
            import mlx  # noqa: F401
            env["mlx_available"] = True
        except ImportError:
            env["mlx_available"] = False

    # CUDA detection
    try:
        result = sp.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            env["cuda_gpus"] = [line.strip() for line in result.stdout.strip().split("\n")]
    except (FileNotFoundError, sp.TimeoutExpired):
        pass

    # PyTorch backend detection
    try:
        import torch
        env["torch_version"] = torch.__version__
        env["torch_cuda"] = torch.cuda.is_available()
        if hasattr(torch.backends, "mps"):
            env["torch_mps"] = torch.backends.mps.is_available()
    except ImportError:
        pass

    # Available RAM
    try:
        import psutil
        env["ram_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
    except ImportError:
        pass

    # Available tools
    for tool in ["uv", "git"]:
        env[f"has_{tool}"] = shutil.which(tool) is not None

    return env


def _format_environment(env: dict) -> str:
    """Format detected environment as a readable string for the prompt."""
    lines = ["[Detected Runtime Environment]"]
    lines.append(f"OS: {env.get('os', 'unknown')} ({env.get('arch', 'unknown')})")
    lines.append(f"Python: {env.get('python', 'unknown')}")

    if env.get("apple_silicon"):
        lines.append("Hardware: Apple Silicon (arm64)")
        lines.append(f"  MLX available: {env.get('mlx_available', False)}")

    if env.get("cuda_gpus"):
        lines.append(f"CUDA GPUs: {', '.join(env['cuda_gpus'])}")

    if "torch_version" in env:
        backends = []
        if env.get("torch_cuda"):
            backends.append("CUDA")
        if env.get("torch_mps"):
            backends.append("MPS")
        if not backends:
            backends.append("CPU only")
        lines.append(f"PyTorch: {env['torch_version']} (backends: {', '.join(backends)})")

    if "ram_gb" in env:
        lines.append(f"RAM: {env['ram_gb']} GB")

    return "\n".join(lines)


GITIGNORE_CONTENT = """\
results-*.tsv
run.log
__pycache__/
*.pyc
.venv/
uv.lock
"""


def _extract_json(text: str) -> str:
    """Extract JSON from text that may be wrapped in markdown code fences."""
    import re

    # Try raw text first
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped

    # Extract from ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if m:
        return m.group(1).strip()

    return stripped


def _call_claude(prompt: str, system_prompt: str = "") -> str:
    """Bridge async Claude Agent SDK call to sync."""
    try:
        raw = asyncio.run(_call_claude_async(prompt, system_prompt))
        return _extract_json(raw)
    except Exception as e:
        logger.error(f"Claude call failed: {e}")
        raise


async def _call_claude_async(prompt: str, system_prompt: str) -> str:
    saved = os.environ.pop("CLAUDECODE", None)
    try:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            permission_mode="bypassPermissions",
            allowed_tools=[],
            cwd=Path.cwd(),
        )
        last_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        last_text = block.text.strip()
        return last_text or "{}"
    finally:
        if saved is not None:
            os.environ["CLAUDECODE"] = saved


class ExperimentWizard:
    """Two-phase wizard: analyze a description, then generate project files."""

    def analyze(self, description: str) -> dict:
        """Phase 1: send description to Claude, return parsed JSON with inferred + uncertain."""
        env = _detect_environment()
        env_str = _format_environment(env)
        prompt = f"{description}\n\n{env_str}"
        raw = _call_claude(prompt, system_prompt=ANALYZE_SYSTEM_PROMPT)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"Claude returned non-JSON: {raw[:200]}")
            raise

    def generate(self, description: str, decisions: dict, dest: Path) -> str:
        """Phase 2: send decisions to Claude, write files, return summary."""
        import re

        env = _detect_environment()
        prompt = json.dumps({
            "description": description,
            "decisions": decisions,
            "environment": env,
        })

        # Build system prompt with scaffold reference
        scaffold_ref = _load_scaffold_reference()
        system = GENERATE_SYSTEM_PROMPT
        if scaffold_ref:
            system += "\n\n## Crucible Reference\n\n" + scaffold_ref

        raw = _call_claude(prompt, system_prompt=system)
        result = json.loads(raw)

        # Validate that files contain real content, not placeholders
        placeholder_re = re.compile(
            r"^\[.*\]$"          # [description in brackets]
            r"|^<\w+>$"         # <written>, <content>, <code>
            r"|^\.\.\.$"        # ...
            r"|^#\s*see above$" # # see above
        , re.IGNORECASE)
        for rel_path, content in result["files"].items():
            stripped = content.strip()
            if placeholder_re.match(stripped) or len(stripped) < 20:
                raise ValueError(
                    f"Generated file '{rel_path}' contains placeholder content "
                    f"({stripped[:50]!r}). Claude failed to produce real code. "
                    f"Try running the wizard again."
                )

        # Write each file
        for rel_path, content in result["files"].items():
            full_path = dest / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

        # Create .gitignore
        (dest / ".gitignore").write_text(GITIGNORE_CONTENT)

        return result["summary"]
