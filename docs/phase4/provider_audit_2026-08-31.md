# Provider Audit — boxs-51/assistant — 2026-08-31

## Baseline reviewed

GitHub `main` at commit `25b137c7310fae7651fd87efe966f8c13647efe6` (`sua vai loi co ban`).

## P0 — causes of the reported live failures

### 1. `ApiType.MODEL` aliases `ApiType.MODELS`

`src/provider/core/api.py` defines both enum members with the same value `"models"`.
Python therefore aliases `MODEL` to `MODELS`. In `GOOGLE_API_MAP`, the later mapping for `ApiType.MODEL` overwrites the list-models mapping, so `ApiType.MODELS` resolves to:

`v1beta/models/{model}`

A list request supplies no `model`, causing `KeyError: 'model'` and the observed 404.

**Fix:** give `MODEL` a distinct enum value and retain the semantic endpoint mapping in each provider.

### 2. Gemini embedding capability discovery sends the wrong keyword

`ModelCapabilityManager._refresh_cache()` calls:

`provider.models.model(model_name=target_model_name, ...)`

But `GoogleModels.model()` reads `model_id` from kwargs. A capability check on an uncached embedding model therefore fails before the embedding request is executed.

**Fix:** canonicalize the detail-model argument to `model_id`.

### 3. Gemini embeddings ignore the caller model

`GoogleEmbeddings.embeddings()` hardcodes `embedding-001`, while the live test uses `gemini-embedding-001` (the currently documented Gemini embedding model).

**Fix:** honor `body["model"]`, normalize an optional `models/` prefix, and default to `gemini-embedding-001`.

### 4. Gemini embeddings converter is called with the wrong keyword

The current call site uses `request_embeddings=...`, while the converter signature accepts `request`. This is an API-contract mismatch.

**Fix:** use the positional/`request=` contract.

### 5. Gemini single/batch request body contract is incorrect

The Gemini embedding REST API puts the model in the path. For `batchEmbedContents`, each batch item also carries a `model` resource name. The single-request body should contain `content`, while batch items should contain `model` + `content`.

**Fix:** remove root `model` for single `embedContent`; keep `models/<id>` on each batch request.

### 6. Gemini embedding response is not normalized

The live gateway contract expects OpenAI-style `data[].embedding`, but the adapter currently returns raw Gemini JSON. Even after fixing HTTP execution, the live test would fail on response shape.

**Fix:** normalize both `embedding.values` and `embeddings[].values` into Gateway `data[]` objects.

## P1 — adjacent provider/runtime defects found during the same audit

### 7. `httpx.Response.json()` is synchronous

Several provider adapters incorrectly use `await response.json()`:

- `src/provider/openai/__init__.py`
- `src/provider/openai/converters/chats/response.py`
- `src/provider/ollama/api/models.py`
- `src/provider/ollama/converters/chat/response.py`

This becomes a runtime `TypeError` after successful HTTP responses.

### 8. OpenAI model handler contract is inconsistent

`ModelOperationHandler` expects `provider.models.models()` / `provider.models.model()`, but `OpenAIProvider` exposes methods directly on the provider instance and does not create a `models` component.

The current provider-specific `ModelInfo` construction also omits required fields from the Gateway schema.

### 9. Ollama capability lookup passes the wrong object

`OllamaModels.models()` passes `self` into `get_capabilities_for_model()`, but that manager expects the actual `BaseProvider` so it can access the provider model mapper and name.

### 10. Storage async contract bug

`SqlAlchemyUnitOfWork.session` is an `AsyncSession`, but `StorageEventFactory.create_events_from_session()` is synchronous and calls `session.flush()` without awaiting it. This directly explains:

`RuntimeWarning: coroutine 'AsyncSession.flush' was never awaited`

**Fix:** make the factory async and `await session.flush()`; await it from `commit()`.

### 11. Provider API key is exposed in logs

The current Gemini auth strategy appends the API key to the URL, while `BaseProvider.send()` logs the authenticated URL. The supplied runtime log therefore contains a live API key in plaintext.

**Fix:** use `x-goog-api-key` header auth for Gemini and mask query credentials in logs. The exposed key should be revoked/rotated.

### 12. Router event subscriptions leak

`models_router` and `embeddings_router` subscribe per request but never unsubscribe their anonymous callbacks. This causes unbounded handler accumulation over the process lifetime.

**Fix:** always unsubscribe in `finally`.

### 13. Gemini image generation is a broken latent path

`GoogleProvider.image_generation()` references `self.adapter`, which is not defined in the provider assembly shown by the current repository.

**Fix:** keep the capability explicitly `NotImplemented` until a real Imagen adapter is wired, rather than failing with `AttributeError`.

## Provider contract recommended going forward

Use one canonical provider model interface everywhere:

- `provider.models.models(http_client, timeout) -> ModelList`
- `provider.models.model(model_id, http_client, timeout) -> ModelInfo`
- `provider.embeddings.embeddings(body, http_client, timeout) -> normalized Gateway embedding payload`

Avoid provider-specific keyword aliases such as `model_name` vs `model_id`.

Keep HTTP transport responsibilities in `BaseProvider.send()` and provider adapters focused on request/response conversion.

## Verification strategy

Add unit tests for:

1. `ApiType.MODEL is not ApiType.MODELS`.
2. Gemini `ApiType.MODELS` builds `/v1beta/models` without a model variable.
3. Gemini `ApiType.MODEL` builds `/v1beta/models/<id>` with a model variable.
4. Gemini batch embedding request contains `models/gemini-embedding-001` in each request item.
5. Gemini single embedding request does not put the model in the root body.
6. Gemini embedding response normalizes to `data[].embedding`.

Then rerun the live suite:

`py -m pytest -q tests/live/test_live_gemini_gateway.py`

The expected targeted result is that the model-list 404 and embedding 503 disappear; the remaining live tests should be judged independently from those two failure roots.
