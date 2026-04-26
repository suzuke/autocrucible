"""Reporter banner copy — single source of truth (M3 PR 17).

Reviewer round 1 Q3: define banner copy in ONE module so static and
interactive renderers stay in sync. Spec §INV-1 wording rules apply
("no bypass observed in N adversarial trials" not "secure"); this
file is the audit point when wording evolves.

Banners surface AttemptNode metadata fields written by the
SubscriptionCLIBackend (M3 PR 16):
  - `isolation == "cli_subscription_unsandboxed"`
  - `compliance_report_path is None` (i.e. `allow_stale_compliance` was used)

If neither condition holds, no banner is rendered (the run was on a
non-degraded backend).
"""

from __future__ import annotations

import html
from typing import Iterable, Optional


# --- Banner text (the single source of truth for §INV-1 wording) ---

# Subscription CLI runs are unsandboxed by design (spec §3.3 — CLI is a
# "complete agent product"). This banner is honest about it. Avoid
# "secure" / "isolated" / "no bypass observed" wording without grounding.
UNSANDBOXED_HEADING = "⚠ Subscription CLI run — degraded ACL"
UNSANDBOXED_BODY = (
    "The agent had unrestricted host filesystem access during this run. "
    "Crucible's CheatResistancePolicy ACL did NOT constrain it. "
    "See spec §3.3 for the full rationale."
)

# When `allow_stale_compliance: true` was used, the gate was bypassed.
# Trial results are diagnostic only — NOT a containment claim.
STALE_COMPLIANCE_HEADING = "⚠ Compliance gate bypassed"
STALE_COMPLIANCE_BODY = (
    "This run was conducted with `experimental.allow_stale_compliance: true`. "
    "Trial results are diagnostic only and do NOT constitute a "
    "containment claim per spec §INV-1."
)


# --- Public API ---


def needs_unsandboxed_banner(metadata: Optional[dict]) -> bool:
    """Return True iff the AttemptNode metadata indicates a degraded-ACL run."""
    if not metadata:
        return False
    return metadata.get("isolation") == "cli_subscription_unsandboxed"


def needs_stale_compliance_banner(metadata: Optional[dict]) -> bool:
    """Return True iff the metadata indicates the compliance gate was bypassed.

    Heuristic: when the backend bypasses via `allow_stale_compliance`,
    `compliance_report_path` is None. When a passing report was found,
    the path is set.
    """
    if not metadata:
        return False
    if metadata.get("isolation") != "cli_subscription_unsandboxed":
        return False
    return metadata.get("compliance_report_path") is None


def render_banners_html(metadatas: Iterable[Optional[dict]]) -> str:
    """Render warning banners as HTML, given an iterable of node metadata.

    Returns "" when no banner is needed (the common case for non-CLI
    runs). When multiple AttemptNodes share the same isolation status
    (the typical case — all attempts in a run use the same backend),
    the banner is rendered once.

    Static and interactive renderers BOTH call this helper. The
    returned HTML is a self-contained `<div>` with inline `style=` —
    no CSS class dependencies on the host page.
    """
    metadatas = list(metadatas)
    show_unsandboxed = any(needs_unsandboxed_banner(m) for m in metadatas)
    show_stale = any(needs_stale_compliance_banner(m) for m in metadatas)

    if not show_unsandboxed and not show_stale:
        return ""

    parts = ['<div style="margin: 12px 0;">']
    if show_unsandboxed:
        parts.append(_banner_box(UNSANDBOXED_HEADING, UNSANDBOXED_BODY))
    if show_stale:
        parts.append(_banner_box(STALE_COMPLIANCE_HEADING, STALE_COMPLIANCE_BODY))
    parts.append("</div>")
    return "".join(parts)


def _banner_box(heading: str, body: str) -> str:
    """Render one banner as a self-contained div."""
    return (
        '<div role="alert" '
        'style="background:#fff3e0;border-left:4px solid #ef6c00;'
        'padding:12px 16px;border-radius:4px;margin-bottom:8px;'
        'color:#212121;">'
        f'<strong style="display:block;margin-bottom:4px;color:#bf360c;">'
        f'{html.escape(heading)}</strong>'
        f'<span style="font-size:13px;">{html.escape(body)}</span>'
        '</div>'
    )
