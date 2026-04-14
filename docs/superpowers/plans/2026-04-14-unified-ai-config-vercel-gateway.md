# Unified AI Config + Vercel AI Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate duplicate AI model configuration in the curation adapter UI and add Vercel AI Gateway as a supported provider.

**Architecture:** Remove AI config fields from the curation connection template (they duplicate the Integrations tab and both write to the same `tenant.ai_config`). Replace with a read-only status line linking to Integrations. Add Vercel as a first-class provider in the AI factory, config, and admin UI with free-text model input.

**Tech Stack:** Python (Pydantic AI, Flask templates), JavaScript (vanilla), Jinja2

---

### Task 1: Add Vercel provider to AI config and factory

**Files:**
- Modify: `src/services/ai/config.py:63-69`
- Modify: `src/services/ai/factory.py:186-202`
- Test: `tests/unit/test_ai_service_factory.py`

- [ ] **Step 1: Write failing tests for Vercel provider**

Add these tests to `tests/unit/test_ai_service_factory.py`:

```python
# In class TestGetPlatformDefaults:

def test_vercel_api_key_from_env(self):
    """Platform defaults resolve Vercel API key from environment."""
    with patch.dict(
        os.environ,
        {
            "PYDANTIC_AI_PROVIDER": "vercel",
            "PYDANTIC_AI_MODEL": "anthropic/claude-sonnet-4-5",
            "VERCEL_AI_GATEWAY_API_KEY": "vercel-test-key",
        },
        clear=False,
    ):
        defaults = get_platform_defaults()
        assert defaults["provider"] == "vercel"
        assert defaults["api_key"] == "vercel-test-key"
```

```python
# In class TestAIServiceFactory:

def test_create_model_vercel_provider(self):
    """Factory creates OpenAIChatModel with VercelProvider for vercel provider."""
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

def test_create_model_vercel_without_key(self):
    """Factory creates Vercel model without explicit key (uses env var)."""
    from pydantic_ai.models.openai import OpenAIChatModel

    with patch.dict(
        os.environ,
        {"VERCEL_AI_GATEWAY_API_KEY": "env-vercel-key"},
        clear=True,
    ):
        factory = AIServiceFactory()
        tenant_config = {
            "provider": "vercel",
            "model": "openai/gpt-4o",
        }
        model = factory.create_model(tenant_ai_config=tenant_config)
        assert isinstance(model, OpenAIChatModel)
```

```python
# In class TestBuildModelString:

def test_vercel_provider(self):
    """Vercel uses vercel prefix."""
    result = build_model_string("vercel", "anthropic/claude-sonnet-4-5")
    assert result == "vercel:anthropic/claude-sonnet-4-5"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_ai_service_factory.py::TestGetPlatformDefaults::test_vercel_api_key_from_env tests/unit/test_ai_service_factory.py::TestAIServiceFactory::test_create_model_vercel_provider tests/unit/test_ai_service_factory.py::TestAIServiceFactory::test_create_model_vercel_without_key tests/unit/test_ai_service_factory.py::TestBuildModelString::test_vercel_provider -v`

Expected: FAIL — `test_vercel_api_key_from_env` fails (vercel not in env var map), vercel model tests fail (no vercel branch in factory)

- [ ] **Step 3: Add Vercel to `_get_provider_api_key` env var map**

In `src/services/ai/config.py`, add `"vercel"` to the `provider_env_vars` dict at line 63-69:

```python
provider_env_vars = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "bedrock": "AWS_ACCESS_KEY_ID",  # Bedrock uses AWS credentials
    "vercel": "VERCEL_AI_GATEWAY_API_KEY",
}
```

- [ ] **Step 4: Add Vercel branch to `_create_provider_model`**

In `src/services/ai/factory.py`, add a new `elif` branch before the `else` fallback (before line 194):

```python
elif provider == "vercel":
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.vercel import VercelProvider

    if api_key:
        return OpenAIChatModel(model_name, provider=VercelProvider(api_key=api_key))
    return OpenAIChatModel(model_name, provider=VercelProvider())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_ai_service_factory.py -v`

Expected: ALL PASS (including all existing tests)

- [ ] **Step 6: Run quality gate**

Run: `uv run mypy src/services/ai/config.py src/services/ai/factory.py --config-file=mypy.ini && uv run ruff check src/services/ai/config.py src/services/ai/factory.py`

Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add src/services/ai/config.py src/services/ai/factory.py tests/unit/test_ai_service_factory.py
git commit -m "feat: add Vercel AI Gateway as a supported AI provider"
```

---

### Task 2: Add Vercel to admin UI provider list

**Files:**
- Modify: `src/admin/blueprints/settings.py:771-800` (provider_info dict)
- Modify: `src/admin/blueprints/settings.py:802-813` (inject vercel into by_provider)
- Modify: `templates/tenant_settings.html:2355-2359` (providerOrder array)

- [ ] **Step 1: Add Vercel to `provider_info` dict**

In `src/admin/blueprints/settings.py`, add vercel to the `provider_info` dict after the existing gateway providers (after line 799, before the closing `}`):

```python
        "gateway/groq": {"name": "Gateway: Groq", "key_url": "https://ai.pydantic.dev/gateway", "gateway": True},
        # Vercel AI Gateway
        "vercel": {
            "name": "Vercel AI Gateway",
            "key_url": "https://vercel.com/docs/ai-gateway",
            "gateway": True,
        },
    }
```

- [ ] **Step 2: Inject Vercel into `by_provider` if absent**

In `src/admin/blueprints/settings.py`, after the `for provider in by_provider:` sorting loop (after line 768) and before the `# Define provider metadata for UI` comment (line 770), add:

```python
    # Sort models within each provider
    for provider in by_provider:
        by_provider[provider] = sorted(set(by_provider[provider]))

    # Inject providers that won't appear in KnownModelName (dynamic model lists)
    if "vercel" not in by_provider:
        by_provider["vercel"] = []
```

- [ ] **Step 3: Add Vercel to `providerOrder` in tenant_settings.html**

In `templates/tenant_settings.html`, update the `providerOrder` array (around line 2355-2359) to include vercel:

```javascript
const providerOrder = [
    'google-gla', 'anthropic', 'openai', 'groq', 'mistral', 'deepseek', 'grok',
    'cohere', 'bedrock', 'google-vertex', 'huggingface', 'cerebras', 'moonshotai', 'heroku',
    // Gateway providers at the end
    'gateway/anthropic', 'gateway/openai', 'gateway/bedrock', 'gateway/google-vertex', 'gateway/groq',
    'vercel'
];
```

- [ ] **Step 4: Run quality gate**

Run: `uv run ruff check src/admin/blueprints/settings.py && uv run mypy src/admin/blueprints/settings.py --config-file=mypy.ini`

Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add src/admin/blueprints/settings.py templates/tenant_settings.html
git commit -m "feat: add Vercel AI Gateway to admin UI provider list"
```

---

### Task 3: Free-text model input for Vercel

**Files:**
- Modify: `templates/tenant_settings.html:2296-2309` (model form group)
- Modify: `templates/tenant_settings.html:2422-2457` (updateModelOptions function)

- [ ] **Step 1: Replace model `<select>` with a container that can switch between select and input**

In `templates/tenant_settings.html`, replace the model form group (lines 2304-2309) with:

```html
                    <div class="form-group">
                        <label for="ai_model">Model</label>
                        <div id="ai_model_container">
                            <select id="ai_model" name="ai_model">
                                <option value="">Select a provider first</option>
                            </select>
                        </div>
                        <small>Select the specific model to use. <span id="model-count"></span></small>
                    </div>
```

- [ ] **Step 2: Update `updateModelOptions()` to handle free-text providers**

In `templates/tenant_settings.html`, replace the `updateModelOptions()` function (lines 2422-2457) with:

```javascript
function updateModelOptions() {
    if (!aiModelsData) return;

    const provider = document.getElementById('ai_provider').value;
    const container = document.getElementById('ai_model_container');
    const apiKeyLink = document.getElementById('api-key-link');

    if (!provider || !aiModelsData[provider]) {
        container.innerHTML = '<select id="ai_model" name="ai_model"><option value="">Select a provider first</option></select>';
        document.getElementById('model-count').textContent = '';
        return;
    }

    const providerInfo = aiModelsData[provider];
    const models = providerInfo.models || [];

    if (models.length === 0) {
        // Free-text input for providers with dynamic model lists (e.g. Vercel)
        const currentVal = (provider === currentProvider) ? currentModel : '';
        container.innerHTML = '<input type="text" id="ai_model" name="ai_model" value="' + currentVal + '" placeholder="e.g. anthropic/claude-sonnet-4-5">';
        document.getElementById('model-count').textContent = 'Enter model name manually';
    } else {
        // Dropdown for providers with known model lists
        let selectHtml = '<select id="ai_model" name="ai_model">';
        models.forEach(function(model) {
            const selected = (model === currentModel) ? ' selected' : '';
            selectHtml += '<option value="' + model + '"' + selected + '>' + model + '</option>';
        });
        selectHtml += '</select>';
        container.innerHTML = selectHtml;
        document.getElementById('model-count').textContent = providerInfo.models.length + ' models available';
    }

    // Update API key link
    if (providerInfo.key_url) {
        apiKeyLink.href = providerInfo.key_url;
        apiKeyLink.style.display = 'inline';
        apiKeyLink.textContent = 'Get ' + providerInfo.name + ' key';
    } else {
        apiKeyLink.style.display = 'none';
    }
}
```

- [ ] **Step 3: Manual verification**

Start the app locally and navigate to the Integrations → AI Services section:
1. Select "Vercel AI Gateway" from the provider dropdown
2. Verify the model field switches to a free-text input with placeholder `e.g. anthropic/claude-sonnet-4-5`
3. Select "Google Gemini" and verify it switches back to a dropdown with model options
4. Type a model name in the Vercel free-text field and save — verify it persists

- [ ] **Step 4: Commit**

```bash
git add templates/tenant_settings.html
git commit -m "feat: free-text model input for Vercel and other dynamic providers"
```

---

### Task 4: Remove AI fields from curation config, add read-only status

**Files:**
- Modify: `templates/adapters/curation/connection_config.html:120-156` (remove AI fields)
- Modify: `templates/adapters/curation/connection_config.html:214-246` (simplify saveCurationConfig JS)

- [ ] **Step 1: Replace AI Segment Ranking section with read-only status**

In `templates/adapters/curation/connection_config.html`, replace lines 120-156 (the "AI Segment Ranking" section including h4, description paragraph, provider dropdown, model input, and API key field) with:

```html
        <h4 style="margin-top: 2rem; margin-bottom: 1rem;">AI Segment Ranking</h4>
        <div style="background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 1rem; margin-bottom: 1rem;">
            {% if adapter_config and adapter_config.get('ai_api_key_set') %}
                <div style="display: flex; align-items: center; gap: 0.5rem;">
                    <span style="color: #10b981; font-size: 1.1em;">&#10003;</span>
                    <span>
                        <strong>AI Configured</strong> &mdash;
                        {{ adapter_config.get('ai_provider', 'gemini') | title }}
                        {% if adapter_config.get('ai_model') %}({{ adapter_config.get('ai_model') }}){% endif %}
                    </span>
                </div>
                <p style="margin: 0.5rem 0 0; font-size: 0.875rem; color: #6b7280;">
                    AI ranking matches catalog segments to buyer briefs using an LLM.
                    <a href="#integrations" onclick="document.querySelector('[data-section=integrations]').click(); return false;">
                        Change AI settings in Integrations
                    </a>
                </p>
            {% else %}
                <div style="display: flex; align-items: center; gap: 0.5rem;">
                    <span style="color: #f59e0b; font-size: 1.1em;">&#9888;</span>
                    <span><strong>AI Not Configured</strong></span>
                </div>
                <p style="margin: 0.5rem 0 0; font-size: 0.875rem; color: #6b7280;">
                    AI segment ranking is disabled. Configure an AI provider in the
                    <a href="#integrations" onclick="document.querySelector('[data-section=integrations]').click(); return false;">
                        Integrations tab
                    </a>
                    to enable intelligent segment matching.
                </p>
            {% endif %}
        </div>
```

- [ ] **Step 2: Simplify `saveCurationConfig()` to only save adapter config**

In `templates/adapters/curation/connection_config.html`, replace the `saveCurationConfig()` function (lines 194-246) with:

```javascript
function saveCurationConfig() {
    const scriptRoot = '{{ request.script_root }}' || '';
    const tenantId = '{{ tenant_id }}';
    const config = {
        catalog_service_url: document.getElementById('curation_catalog_url').value,
        sales_service_url: document.getElementById('curation_sales_url').value,
        activation_service_url: document.getElementById('curation_activation_url').value,
        pricing_multiplier: parseFloat(document.getElementById('curation_pricing_multiplier').value) || 5,
        pricing_floor_cpm: parseFloat(document.getElementById('curation_pricing_floor').value) || 0.1,
        pricing_max_suggested_cpm: parseFloat(document.getElementById('curation_pricing_max').value) || 10,
        max_media_buys_per_list: parseInt(document.getElementById('max_media_buys_per_list').value, 10) || 500,
        mock_activation: document.getElementById('curation_mock_activation').checked,
        http_timeout_seconds: parseFloat(document.getElementById('curation_http_timeout').value) || 30
    };

    if (!config.catalog_service_url || !config.sales_service_url || !config.activation_service_url) {
        showNotification('All three service URLs are required', 'error');
        return;
    }

    fetch(scriptRoot + '/api/tenant/' + tenantId + '/adapter-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ adapter_type: 'curation', config: config })
    })
    .then(response => {
        if (response.ok) {
            showNotification('Curation configuration saved successfully', 'success');
        } else {
            return Promise.reject(response);
        }
    })
    .catch(error => {
        showNotification('Error saving configuration: ' + (error.message || 'Unknown error'), 'error');
    });
}
```

- [ ] **Step 3: Manual verification**

Start the app locally, navigate to tenant settings with Curation adapter selected:
1. Verify the AI section shows a read-only status (not editable fields)
2. If AI is configured, verify it shows the provider name and model
3. Click the "Change AI settings in Integrations" link — verify it navigates to the Integrations tab
4. Click "Save Configuration" — verify it saves without errors (no more AI config POST)
5. Go to Integrations tab, change AI provider, save, go back to Curation tab — verify the read-only status reflects the change

- [ ] **Step 4: Run quality gate**

Run: `make quality`

Expected: All checks pass

- [ ] **Step 5: Commit**

```bash
git add templates/adapters/curation/connection_config.html
git commit -m "refactor: remove duplicate AI config from curation adapter, show read-only status"
```
