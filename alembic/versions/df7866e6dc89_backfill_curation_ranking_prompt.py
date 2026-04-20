"""backfill curation ranking prompt

Revision ID: df7866e6dc89
Revises: 018bd7bdeed8
Create Date: 2026-04-21 03:09:15.314006

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "df7866e6dc89"
down_revision = "018bd7bdeed8"
branch_labels = None
depends_on = None


# Keep a literal copy of the default prompt here rather than importing
# from src/adapters/curation/ranking.py. Migrations must be replayable
# against any future codebase, so they cannot depend on runtime modules
# that may have moved or been renamed.
_DEFAULT_CURATION_RANKING_PROMPT = """You are an advertising audience matching expert. \
Your task is to rank candidate audience segments against a campaign brief.

## Your Process

### Step 1: Analyze the Campaign Brief
Extract campaign intent: objective (awareness/consideration/conversion/retention), \
target audience, category/vertical, geographic hints, channels, budget signal, \
timing/seasonality, must-have constraints, nice-to-have preferences, and negative \
constraints (topics, brands, categories to avoid).

### Step 2: Rank Segments Against Extracted Intent
For each candidate segment, evaluate on these criteria:
1. SEMANTIC FIT: Does the segment's audience overlap with the campaign's target \
audience? Examine the description, signals_used, and domains in the ext metadata.
2. INTENT ALIGNMENT: Does the segment capture the right behavior or interest intent \
for the campaign objective?
3. SPECIFICITY BONUS: Prefer segments with specific, high-intent targeting over broad \
segments. Check forecast data — segments with real impression volume and multiple \
unique_sites indicate proven reach.
4. GEOGRAPHIC MATCH: Check if the segment's countries align with the campaign's \
geographic requirements.
5. CONSTRAINT CHECK: Does this segment conflict with any negative constraints, brand \
safety requirements, or excluded topics? If so, exclude or heavily penalize.
6. REACH & QUALITY: Prefer segments with meaningful forecast data (daily impressions \
> 0, unique_sites > 1) over untested segments.

## Scoring Rubric
Use the FULL 0.0-1.0 range with precision. Every segment should receive a distinct \
score reflecting its precise fit.
- 0.9-1.0: Near-perfect semantic and intent match with no conflicts
- 0.7-0.89: Strong match with clear evidence from segment metadata
- 0.5-0.69: Moderate match, useful but not ideal
- 0.3-0.49: Weak match, only marginally relevant
- Below 0.3: Do not return — omit these segments entirely

## Rules
- relevance_explanation must cite CONCRETE evidence from the segment (description, \
signals, domains, countries, forecast). Do NOT use vague phrases like "good fit" or \
"aligns well".
- Pattern: "[Specific signal from segment] matches [specific need from brief]".
- Exclusions and negative constraints are first-class signals — a segment matching \
the audience but violating a constraint should be excluded.
- If fewer segments meet the 0.3 threshold, return fewer. Do not pad the list."""


def upgrade() -> None:
    """Backfill ``product_ranking_prompt`` for existing curation tenants.

    ``CurationAdapter.on_config_saved`` seeds the prompt on future saves, but
    tenants created before that hook shipped have a null value. Set them to
    the default so AI ranking works out of the box.

    Scope: only tenants whose ``adapter_config.adapter_type`` is ``curation``
    AND whose ``product_ranking_prompt`` is null or empty. Never overwrites
    an existing value.
    """
    op.execute(
        sa.text(
            """
            UPDATE tenants
            SET product_ranking_prompt = :prompt
            WHERE (product_ranking_prompt IS NULL OR product_ranking_prompt = '')
              AND tenant_id IN (
                  SELECT tenant_id FROM adapter_config WHERE adapter_type = 'curation'
              )
            """
        ).bindparams(prompt=_DEFAULT_CURATION_RANKING_PROMPT)
    )


def downgrade() -> None:
    """Do not unseed — the prompt is valid data and removing it would
    regress ranking for tenants that accepted the default.

    If reverting is necessary, do it by tenant explicitly via the admin UI.
    """
    pass
