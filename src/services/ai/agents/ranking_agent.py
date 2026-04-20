"""Pydantic AI agent for product ranking based on brief relevance."""

import json
import logging
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, capture_run_messages

from src.core.schemas import Product

logger = logging.getLogger(__name__)


class ProductRanking(BaseModel):
    """Ranking for a single product."""

    product_id: str = Field(..., description="The product ID being ranked")
    relevance_score: float = Field(
        ..., ge=0.0, le=1.0, description="Relevance score from 0 (not relevant) to 1 (highly relevant)"
    )
    reason: str = Field(..., description="Brief explanation of why this product is relevant/not relevant")


class ProductRankingResult(BaseModel):
    """Structured output from AI product ranking."""

    rankings: list[ProductRanking] = Field(..., description="List of products with their relevance rankings")


RANKING_SYSTEM_PROMPT = """You are a product ranking assistant for an advertising platform.

Your job is to rank advertising products based on how well they match a buyer's brief/requirements.

For each product, provide:
- A relevance_score from 0.0 to 1.0 (higher = more relevant)
- A brief reason explaining the relevance

Consider factors like:
- How well the product's name/description matches the brief
- Format suitability for the campaign goals
- Audience targeting alignment
- Any other relevant product attributes

Be objective and consistent in your scoring."""


DEFAULT_CURATION_RANKING_PROMPT = """You are an advertising audience matching expert. \
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


def create_ranking_agent(model: Any) -> Agent[None, ProductRankingResult]:
    """Create a product ranking agent with the specified model.

    Args:
        model: Pydantic AI model instance or string

    Returns:
        Configured Agent instance
    """
    return Agent(
        model=model,
        output_type=ProductRankingResult,
        system_prompt=RANKING_SYSTEM_PROMPT,
    )


def build_ranking_prompt(
    custom_prompt: str,
    brief: str,
    products: list[Product],
) -> str:
    """Build the user prompt for product ranking.

    Args:
        custom_prompt: Tenant's custom ranking prompt
        brief: The buyer's brief/requirements
        products: List of Product models to rank

    Returns:
        Formatted prompt string
    """
    simplified_products = []
    for p in products:
        simplified = {
            "product_id": p.product_id,
            "name": p.name,
            "description": p.description,
            "format_ids": [str(f) for f in (p.format_ids or [])],
            "channels": [c.value for c in (p.channels or [])],
            "delivery_type": p.delivery_type.value,
        }
        simplified_products.append(simplified)

    products_str = json.dumps(simplified_products, indent=2)

    return f"""Rank these products based on relevance to the buyer's brief.

{custom_prompt}

Buyer's Brief:
{brief}

Products to Rank:
{products_str}

Provide a relevance_score (0.0-1.0) and brief reason for each product."""


async def rank_products_async(
    agent: Agent[None, ProductRankingResult],
    custom_prompt: str,
    brief: str,
    products: list[Product],
) -> ProductRankingResult:
    """Rank products using the agent.

    Args:
        agent: The ranking agent
        custom_prompt: Tenant's custom ranking prompt
        brief: The buyer's brief
        products: List of Product models

    Returns:
        ProductRankingResult with rankings for each product
    """
    prompt = build_ranking_prompt(custom_prompt, brief, products)
    with capture_run_messages() as run_messages:
        try:
            result = await agent.run(prompt)
        except Exception as exc:
            # Log the full message exchange so we can see exactly what the model returned
            logger.error("AI ranking FAILED — error: %s", getattr(exc, "message", str(exc)))
            logger.error("AI ranking FAILED — full message exchange:\n%s", run_messages)
            raise

    # Log the model that handled the request and full response for debugging
    messages = result.all_messages()
    for msg in messages:
        if hasattr(msg, "model_name"):
            logger.info("AI ranking handled by model: %s", msg.model_name)
    logger.debug("AI ranking full response messages:\n%s", messages)

    # pydantic-ai 1.x uses .output for structured data
    return result.output
