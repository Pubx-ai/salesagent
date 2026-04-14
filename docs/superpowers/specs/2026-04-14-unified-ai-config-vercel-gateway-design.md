# Unified AI Config + Vercel AI Gateway Support

## Problem

AI model configuration is duplicated in two admin UI locations:

1. **Integrations tab** (`tenant_settings.html`) — full dynamic provider list with 15+ providers, loaded from Pydantic AI's `KnownModelName`
2. **Curation adapter config** (`connection_config.html`) — hardcoded 3-option dropdown (Gemini, Anthropic, OpenAI)

Both forms POST to the same `/settings/ai` endpoint, overwriting each other. The curation adapter itself doesn't even read AI config from `adapter_config` — `products.py` reads directly from `tenant.ai_config`. The duplicate fields are confusing and functionally redundant.

Separately, Pydantic AI natively supports Vercel AI Gateway via `VercelProvider`, but the salesagent UI doesn't expose it because:
- `get_ai_models()` doesn't include `vercel` in `provider_info`
- `_create_provider_model()` in the factory has no vercel branch
- `_get_provider_api_key()` doesn't map the vercel env var

## Solution

### 1. Remove AI fields from Curation adapter config

**File:** `templates/adapters/curation/connection_config.html`

Remove the "AI Segment Ranking" section entirely (lines 120-156: provider dropdown, model input, API key field). Replace with a read-only status block:
- Shows current AI config (provider name + model) from the `adapter_config_dict` context (which already mirrors `tenant.ai_config` via `tenants.py:289-296`)
- Shows "Not configured" if no AI config exists
- Links to the Integrations tab for configuration

**File:** `templates/adapters/curation/connection_config.html` (JS section)

Remove the `saveAiConfig` fetch from `saveCurationConfig()`. Currently the function does `Promise.all([saveAdapterConfig, saveAiConfig])`. After the change, it only saves the adapter config (service URLs, pricing, etc.).

### 2. Add Vercel AI Gateway to the provider list

**File:** `src/admin/blueprints/settings.py` — `get_ai_models()`

Add `vercel` to the `provider_info` dict:
```python
"vercel": {
    "name": "Vercel AI Gateway",
    "key_url": "https://vercel.com/docs/ai-gateway",
    "gateway": True,
},
```

Since Vercel models are dynamic (it proxies other providers), they won't appear in `KnownModelName`. After building `by_provider` from KnownModelName, inject vercel if absent:
```python
if "vercel" not in by_provider:
    by_provider["vercel"] = []
```

This ensures vercel appears in the dropdown even with an empty model list.

**File:** `src/services/ai/factory.py` — `_create_provider_model()`

Add a vercel branch:
```python
elif provider == "vercel":
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.vercel import VercelProvider

    if api_key:
        return OpenAIChatModel(model_name, provider=VercelProvider(api_key=api_key))
    return OpenAIChatModel(model_name, provider=VercelProvider())
```

**File:** `src/services/ai/config.py` — `_get_provider_api_key()`

Add vercel env var mapping:
```python
"vercel": "VERCEL_AI_GATEWAY_API_KEY",
```

### 3. Free-text model input for Vercel

**File:** `templates/tenant_settings.html` — `updateModelOptions()` JS function

When the selected provider is `vercel` (or any provider with an empty model list), replace the model `<select>` with a text `<input>` element:
- Placeholder: `e.g. anthropic/claude-sonnet-4-5`
- The input uses the same `id="ai_model"` and `name="ai_model"` so the form submission works unchanged

When switching to a provider that has models, restore the `<select>` dropdown.

Add `vercel` to the `providerOrder` array (in the gateway section at the end).

## Files Changed

| File | Change |
|------|--------|
| `templates/adapters/curation/connection_config.html` | Remove AI fields, add read-only status + link |
| `src/admin/blueprints/settings.py` | Add vercel to `provider_info`, inject into `by_provider` |
| `src/services/ai/factory.py` | Add vercel branch in `_create_provider_model()` |
| `src/services/ai/config.py` | Add vercel env var mapping |
| `templates/tenant_settings.html` | Free-text input for vercel, add to `providerOrder` |

## Testing

- **Unit test:** Verify `_create_provider_model("vercel", "anthropic/claude-sonnet-4-5", "key")` returns an `OpenAIChatModel` with `VercelProvider`
- **Unit test:** Verify `_get_provider_api_key("vercel")` reads `VERCEL_AI_GATEWAY_API_KEY`
- **Manual test:** Curation config page shows read-only AI status with link to Integrations
- **Manual test:** Integrations page shows Vercel AI Gateway in dropdown, allows free-text model entry
- **Manual test:** Saving AI config from Integrations is reflected in curation config read-only status

## Out of Scope

- Vercel OIDC token authentication (API key only for now)
- Curation adapter reading AI config from its own config (it correctly uses `tenant.ai_config`)
- Changes to the AI ranking logic itself
