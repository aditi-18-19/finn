# FINN backend deployment on Render

## Repository settings

- Runtime: Python
- Root directory: leave blank (repository root)
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`

## Environment variables

Set only the variables required by the enabled features. Add secrets in the Render dashboard, never in Git.

- `GEMINI_API_KEY`: required for Gemini-backed extraction / analysis features.
- `OPENAI_API_KEY`: only if an enabled pipeline path uses OpenAI.
- `TAVILY_API_KEY`: only if an enabled pipeline path uses Tavily.

The offline dashboard endpoints should remain usable from the committed JSON snapshot without provider keys. Live research and model-backed features may fail if provider credentials, quotas, model access, or writable disk are unavailable.

## Persistent storage and research

The on-demand research pipeline downloads PDFs, creates chunks and embedding indexes, and writes research outputs under `data/`. Render's default filesystem may be ephemeral. Do not treat generated research artifacts as durable unless a persistent disk is configured and the application paths are mounted to it.

The research pipeline can also download an embedding model on first use. This may increase startup/request time and memory usage. Test `/research/{symbol}` separately after deployment; a successful `/health` check does not prove live research works.

## CORS

Set `FINN_CORS_ORIGINS` to the exact deployed Vercel origin, for example:

`https://your-finn-app.vercel.app`

Do not include a trailing slash unless the API's CORS parsing explicitly expects it.

## Smoke tests

After deployment, verify:

1. `GET /health`
2. `GET /feed`
3. `GET /alerts`
4. `GET /company/RELIANCE`

Then test the Vercel frontend. Treat `/ask` and `/research/{symbol}` as separate live-AI integration tests, not as part of the offline snapshot health check.
