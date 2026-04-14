# Vercel AI Gateway Fallback Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Vercel AI Gateway tenants to configure fallback models that are tried if the primary model fails.

**Architecture:** Add `fallback_models` field to `TenantAIConfig`. When the vercel factory branch sees fallback models, it wraps primary + fallbacks in Pydantic AI's native `FallbackModel`. The admin UI shows a conditional fallback field only when Vercel is selected.

**Tech Stack:** Python (Pydantic AI `FallbackModel`, `VercelProvider`), JavaScript (DOM API), Jinja2

---

### Task 1: Add fallback_models to TenantAIConfig and wire through factory

**Files:**
- Modify: `src/services/ai/config.py:16-37`
- Modify: `src/services/ai/factory.py:79-129` (create_model)
- Modify: `src/services/ai/factory.py:194-200` (vercel branch)
- Test: `tests/unit/test_ai_service_factory.py`

- [ ] **Step 1: Write failing tests**

Add these tests to `tests/unit/test_ai_service_factory.py`:

```python
# In class TestTenantAIConfig:

def test_fallback_models_default_none(self):
    """Fallback models default to None."""
    config = TenantAIConfig()
    assert config.fallback_models is None

def test_fallback_models_parsed_from_dict(self):
    """Fallback models can be parsed from database dict."""
    config = TenantAIConfig.model_validate(
        {
            "provider": "vercel",
            "model": "anthropic/claude-sonnet-4-5",
            "fallback_models": ["openai/gpt-4o", "google/gemini-2.0-flash"],
        }
    )
    assert config.fallback_models == ["openai/gpt-4o", "google/gemini-2.0-flash"]
```

```python
# In class TestAIServiceFactory:

def test_create_model_vercel_with_fallbacks(self):
    """Factory creates FallbackModel when vercel has fallback_models."""
    from pydantic_ai.models.fallback import FallbackModel

    with patch.dict(os.environ, {}, clear=True):
        factory = AIServiceFactory()
        tenant_config = {
            "provider": "vercel",
            "model": "anthropic/claude-sonnet-4-5",
            "api_key": "vercel-key",
            "fallback_models": ["openai/gpt-4o", "google/gemini-2.0-flash"],
        }
        model = factory.create_model(tenant_ai_config=tenant_config)
        assert isinstance(model, FallbackModel)

def test_create_model_vercel_without_fallbacks_unchanged(self):
    """Factory returns OpenAIChatModel when vercel has no fallback_models."""
    from pydantic_ai.models.openai import OpenAIChatModel

    with patch.dict(os.environ, {}, clear=True):
        factory = AIServiceFactory()
        tenant_config = {
            "provider": "vercel",
            "model": "anthropic/claude-sonnet-4-5",
            "api_key": "vercel-key",
        }
        model = factory.create_model(tenant_ai_config=tenant_config)
        assert isinstance(model, OpenAIChatModel)

def test_fallback_models_ignored_for_non_vercel(self):
    """Fallback models in config are ignored for non-vercel providers."""
    from pydantic_ai.models.google import GoogleModel

    with patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "test-key"},
        clear=True,
    ):
        factory = AIServiceFactory()
        tenant_config = {
            "provider": "gemini",
            "model": "gemini-2.0-flash",
            "api_key": "test-key",
            "fallback_models": ["openai/gpt-4o"],
        }
        model = factory.create_model(tenant_ai_config=tenant_config)
        # Should be GoogleModel, not FallbackModel
        assert isinstance(model, GoogleModel)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_ai_service_factory.py::TestTenantAIConfig::test_fallback_models_default_none tests/unit/test_ai_service_factory.py::TestTenantAIConfig::test_fallback_models_parsed_from_dict tests/unit/test_ai_service_factory.py::TestAIServiceFactory::test_create_model_vercel_with_fallbacks tests/unit/test_ai_service_factory.py::TestAIServiceFactory::test_create_model_vercel_without_fallbacks_unchanged tests/unit/test_ai_service_factory.py::TestAIServiceFactory::test_fallback_models_ignored_for_non_vercel -v`

Expected: Config tests may pass (extra="ignore" allows unknown fields but won't set them as attributes). Factory tests FAIL because `create_model` doesn't pass fallback_models to the vercel branch.

- [ ] **Step 3: Add `fallback_models` field to `TenantAIConfig`**

In `src/services/ai/config.py`, add after the `api_key` field (after line 31):

```python
    # Fallback models for providers that support it (e.g., Vercel AI Gateway)
    # Models are tried in order if the primary model fails
    fallback_models: list[str] | None = None
```

- [ ] **Step 4: Wire fallback_models through `create_model` to `_create_provider_model`**

In `src/services/ai/factory.py`, update `create_model()` to read fallback_models from the config and pass it to `_create_provider_model`. 

Change line 129 from:
```python
        return self._create_provider_model(provider, model_name, api_key)
```
to:
```python
        fallback_models = config.fallback_models or []
        return self._create_provider_model(provider, model_name, api_key, fallback_models=fallback_models)
```

Update the `_create_provider_model` signature (line 131) from:
```python
    def _create_provider_model(self, provider: str, model_name: str, api_key: str | None) -> Any:
```
to:
```python
    def _create_provider_model(
        self, provider: str, model_name: str, api_key: str | None, *, fallback_models: list[str] | None = None
    ) -> Any:
```

- [ ] **Step 5: Update the vercel branch to use FallbackModel**

In `src/services/ai/factory.py`, replace the vercel branch (lines 194-200) with:

```python
        elif provider == "vercel":
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.vercel import VercelProvider

            vercel_provider = VercelProvider(api_key=api_key) if api_key else VercelProvider()
            primary = OpenAIChatModel(model_name, provider=vercel_provider)

            if fallback_models:
                from pydantic_ai.models.fallback import FallbackModel

                fallbacks = [OpenAIChatModel(m, provider=vercel_provider) for m in fallback_models]
                logger.info("Created Vercel FallbackModel: primary=%s, fallbacks=%s", model_name, fallback_models)
                return FallbackModel(primary, *fallbacks)

            return primary
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_ai_service_factory.py -v`

Expected: ALL 25 tests PASS (20 existing + 5 new)

- [ ] **Step 7: Run quality gate**

Run: `uv run mypy src/services/ai/config.py src/services/ai/factory.py --config-file=mypy.ini && uv run ruff check src/services/ai/config.py src/services/ai/factory.py`

Expected: No errors

- [ ] **Step 8: Commit**

```bash
git add src/services/ai/config.py src/services/ai/factory.py tests/unit/test_ai_service_factory.py
git commit -m "feat: add Vercel AI Gateway fallback model support via FallbackModel"
```

---

### Task 2: Parse fallback_models in settings backend

**Files:**
- Modify: `src/admin/blueprints/settings.py:529-593`

- [ ] **Step 1: Add fallback_models parsing to `update_ai()`**

In `src/admin/blueprints/settings.py`, in the `update_ai()` function, add after line 537 (`logfire_token = ...`):

```python
        fallback_models_raw = request.form.get("fallback_models", "").strip()
```

Then after line 566 (the `if existing_config.get("settings"):` block), add:

```python
            # Handle fallback models (Vercel AI Gateway)
            if fallback_models_raw:
                parsed = [m.strip() for m in fallback_models_raw.split(",") if m.strip()]
                if parsed:
                    new_config["fallback_models"] = parsed
            elif existing_config.get("fallback_models") and not fallback_models_raw:
                # Clear fallback models if the field was submitted empty
                pass  # Don't carry over — user cleared the field
```

- [ ] **Step 2: Run quality gate**

Run: `uv run ruff check src/admin/blueprints/settings.py && uv run mypy src/admin/blueprints/settings.py --config-file=mypy.ini`

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/admin/blueprints/settings.py
git commit -m "feat: parse fallback_models form field in AI settings backend"
```

---

### Task 3: Add fallback models field to admin UI

**Files:**
- Modify: `templates/tenant_settings.html:2268-2271` (Jinja2 variables)
- Modify: `templates/tenant_settings.html:2312-2313` (add form field after model)
- Modify: `templates/tenant_settings.html:2350-2354` (JS variables)
- Modify: `templates/tenant_settings.html:2427-2484` (updateModelOptions)

- [ ] **Step 1: Add Jinja2 variable for current fallback models**

In `templates/tenant_settings.html`, after line 2271 (`{% set current_model = ... %}`), add:

```html
                {% set current_fallback_models = ai_config.get('fallback_models', []) %}
```

- [ ] **Step 2: Add the fallback models form field**

In `templates/tenant_settings.html`, after the model form group closing `</div>` (after line 2312), add:

```html
                    <div class="form-group" id="fallback_models_group" style="display: none;">
                        <label for="fallback_models">Fallback Models (optional)</label>
                        <input type="text" id="fallback_models" name="fallback_models"
                               value="{{ current_fallback_models | join(', ') if current_fallback_models else '' }}"
                               placeholder="e.g. openai/gpt-4o, google/gemini-2.0-flash">
                        <small>Comma-separated list of models to try if the primary model fails. Only available with Vercel AI Gateway.</small>
                    </div>
```

- [ ] **Step 3: Add JS variable for current fallback models**

In `templates/tenant_settings.html`, after line 2354 (`let aiModelsData = null;`), add:

```javascript
                const currentFallbackModels = '{{ current_fallback_models | join(", ") if current_fallback_models else '' }}';
```

- [ ] **Step 4: Toggle fallback field visibility in `updateModelOptions()`**

In `templates/tenant_settings.html`, at the end of the `updateModelOptions()` function, just before the closing `}` (after the API key link block, around line 2484), add:

```javascript
                    // Show/hide fallback models field (Vercel only)
                    const fallbackGroup = document.getElementById('fallback_models_group');
                    if (fallbackGroup) {
                        if (provider === 'vercel') {
                            fallbackGroup.style.display = '';
                            // Populate with saved value on initial load
                            if (provider === currentProvider && currentFallbackModels) {
                                document.getElementById('fallback_models').value = currentFallbackModels;
                            }
                        } else {
                            fallbackGroup.style.display = 'none';
                        }
                    }
```

- [ ] **Step 5: Manual verification**

Start the app locally and navigate to Integrations → AI Services:
1. Select Google Gemini — verify fallback field is hidden
2. Select Vercel AI Gateway — verify fallback field appears with placeholder
3. Enter fallback models, save, reload — verify they persist
4. Switch to OpenAI — verify fallback field hides
5. Switch back to Vercel — verify fallback value is restored

- [ ] **Step 6: Commit**

```bash
git add templates/tenant_settings.html
git commit -m "feat: add fallback models UI field for Vercel AI Gateway"
```
