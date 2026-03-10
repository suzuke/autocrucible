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
You are an experiment scaffold generator. The user will provide a description, resolved decisions,
and a detected runtime environment. Use the environment to generate hardware-appropriate code
(e.g. MPS device for Apple Silicon, CUDA for NVIDIA GPUs, CPU fallback otherwise).
Return ONLY valid JSON with two keys:
- "files": a dict mapping relative file paths to their string contents. Must include:
  .crucible/config.yaml, .crucible/program.md, and any source files needed.
- "summary": a one-line summary of the generated experiment.
Do not include any text outside the JSON object.

CRITICAL: Every file value MUST contain the COMPLETE, REAL source code — not placeholders,
not abbreviations, not "<written>" or "..." or "# see above". Each file must be fully
functional as-is. If the JSON is too large, reduce the code complexity rather than
truncating file contents.

## Architecture Guards (CRITICAL)

The evaluate.py you generate MUST include code-enforced architecture constraints.
The agent will try to maximize the metric by any means — if you only state constraints
in program.md, the agent WILL bypass them. Constraints must be checked in evaluate.py
(which is readonly) and violations must result in metric penalties.

Include these guards in evaluate.py as appropriate:

1. **Code complexity cap**: Use `ast` or line counting on the editable file(s).
   If LOC exceeds a threshold (e.g. 2x the initial size), apply a penalty multiplier.

2. **Required module usage**: If the experiment specifies an approach (e.g. neural network,
   genetic algorithm), verify at runtime that the core module is actually used in
   decision-making — not bypassed by hand-coded rules. For example, instrument the
   model's forward() call and check it was invoked during evaluation.

3. **Banned patterns**: If the approach should learn behavior (not hard-code it),
   use `ast.parse` on the editable file to detect excessive hand-written heuristic
   functions (e.g. more than N function definitions beyond the expected API).

4. **Decision attribution**: Where possible, have the agent's interface return metadata
   about how the decision was made, and verify the stated approach was actually used.

Apply penalties as a multiplier on the primary metric (e.g. `metric *= 0.3` for
violations) rather than zeroing it out, so the agent still gets gradient signal.
"""

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
        env = _detect_environment()
        prompt = json.dumps({
            "description": description,
            "decisions": decisions,
            "environment": env,
        })
        raw = _call_claude(prompt, system_prompt=GENERATE_SYSTEM_PROMPT)
        result = json.loads(raw)

        # Validate that files contain real content, not placeholders
        placeholder_patterns = ["<written>", "<content>", "<code>", "..."]
        for rel_path, content in result["files"].items():
            stripped = content.strip()
            if stripped in placeholder_patterns or len(stripped) < 20:
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
