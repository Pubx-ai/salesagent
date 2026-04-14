# Vercel AI Gateway Fallback Models

## Problem

When using Vercel AI Gateway as the AI provider, there is no way to configure fallback models. If the primary model fails (rate limit, outage, context limit), the AI ranking feature fails entirely. Pydantic AI has native `FallbackModel` support that can chain multiple models — we should expose this for Vercel tenants.

## Solution

Add a `fallback_models` field to `TenantAIConfig` and wire it through the factory, admin UI, and settings backend. Only active when `provider == "vercel"`.

### Data Model

Add to `TenantAIConfig` in `src/services/ai/config.py`:

```python
fallback_models: list[str] | None = None  # e.g. ["openai/gpt-4o", "google/gemini-2.0-flash"]
```

This field is ignored for all non-Vercel providers. The `model_config = {"extra": "ignore"}` on the class ensures forward compatibility.

### Factory

In `AIServiceFactory.create_model()`, read `fallback_models` from the resolved config and pass it to `_create_provider_model()`.

In `_create_provider_model()`, the vercel branch changes:

- **Without fallbacks** (current behavior): returns `OpenAIChatModel(model_name, provider=VercelProvider(...))`
- **With fallbacks**: wraps primary + fallback models in `FallbackModel`:

```python
from pydantic_ai.models.fallback import FallbackModel

primary = OpenAIChatModel(model_name, provider=vercel_provider)
fallbacks = [OpenAIChatModel(m, provider=vercel_provider) for m in fallback_models]
return FallbackModel(primary, *fallbacks)
```

All models in the chain share the same `VercelProvider` instance (same API key). Pydantic AI's `FallbackModel` tries models in order and returns the first successful response.

### Logging

Log at INFO when a FallbackModel is created:
```
logger.info("Created Vercel FallbackModel: primary=%s, fallbacks=%s", model_name, fallback_models)
```

Pydantic AI's `FallbackModel` sets `response.model_name` to the model that actually handled the request. The ranking agent in `products.py` already has access to this via the agent response. No additional logging needed at the call site — the factory log is sufficient to know fallbacks are configured, and Pydantic AI handles the rest.

### Admin UI

In `templates/tenant_settings.html`, when the Vercel provider is selected, show an additional form field below the model input:

- Label: "Fallback Models (optional)"
- Type: text input
- Placeholder: `e.g. openai/gpt-4o, google/gemini-2.0-flash`
- Help text: "Comma-separated list of models to try if the primary model fails. Only available with Vercel AI Gateway."
- Hidden for all non-Vercel providers (toggled by `updateModelOptions()`)

The field uses `name="fallback_models"` and is sent with the form POST.

On page load, if `currentProvider === 'vercel'` and `ai_config.fallback_models` exists, populate the field with the comma-separated list.

### Settings Backend

In `src/admin/blueprints/settings.py` `update_ai()`:

- Read `fallback_models` from the form
- Parse: split by comma, strip whitespace, filter empty strings
- Store as a list in `ai_config["fallback_models"]` (or omit the key if empty)

## Files Changed

| File | Change |
|------|--------|
| `src/services/ai/config.py` | Add `fallback_models: list[str] \| None = None` to `TenantAIConfig` |
| `src/services/ai/factory.py` | Read fallback_models in `create_model()`, pass to `_create_provider_model()`. Vercel branch wraps in `FallbackModel` when fallbacks present. Add INFO log. |
| `src/admin/blueprints/settings.py` | Parse `fallback_models` form field in `update_ai()`, store in ai_config |
| `templates/tenant_settings.html` | Add fallback models input visible only for Vercel, populate from config, send in form |
| `tests/unit/test_ai_service_factory.py` | Test: vercel with fallbacks returns FallbackModel, vercel without fallbacks returns OpenAIChatModel (unchanged), fallback_models ignored for non-vercel providers |

## Testing

- **Unit test:** `create_model(tenant_ai_config={"provider": "vercel", "model": "anthropic/claude-sonnet-4-5", "api_key": "key", "fallback_models": ["openai/gpt-4o"]})` returns a `FallbackModel` instance
- **Unit test:** `create_model(tenant_ai_config={"provider": "vercel", "model": "anthropic/claude-sonnet-4-5", "api_key": "key"})` still returns `OpenAIChatModel` (no regression)
- **Unit test:** `TenantAIConfig(fallback_models=["a", "b"])` parses correctly; `TenantAIConfig()` has `fallback_models=None`
- **Manual test:** Select Vercel in UI, verify fallback field appears. Select Google Gemini, verify it disappears. Save with fallback models, reload, verify they persist.

## Out of Scope

- Fallback support for non-Vercel providers (use Pydantic AI Gateway routing groups instead)
- Per-model settings (temperature, max_tokens) for fallback models
- UI for reordering fallback models (comma order is the priority order)
