# Demo reliability and deployment checklist

## Offline demo
`pipeline/prepare_demo.py` validates the existing JSON artifacts and writes `data/demo_snapshot_manifest.json`. It makes no network calls. The app's `/feed`, `/alerts`, and `/company/{symbol}` endpoints read local JSON files, so those views can be demonstrated without live fetching.

Run before a demo:
```powershell
python pipeline/prepare_demo.py
```
The current research screen is different: on-demand `/research/{symbol}` and `/ask` may call external services and remain subject to provider quota. For the demo, show already-generated research answer JSON if available; do not trigger a new model call during a live presentation unless quota is confirmed.

## Coverage caveat
The existing portfolio feed/price/announcement outputs cover the configured 22-company universe. Primary-document research collection is not yet proven for all 22: the collected/indexed research universe currently includes AXISBANK, BAJFINANCE, BHARTIARTL, TRENT and the RELIANCE Step 16 test. TRENT had no transcript links in the collected Screener page. Do not claim 22-company transcript coverage.

## Refresh
Refresh data manually on a trusted machine before submission using the existing pipeline steps in their documented order. Commit only intended demo JSON outputs; never commit `.env`, API keys, raw cookies or private credentials. Do not schedule a GitHub Action that needs BSE/Screener credentials until secrets and the exact pipeline commands are configured and tested.

## Deployment (manual account steps required)
- Frontend: Vercel, root directory `web`, build command `npm run build`, output handled by Next.js preset. Set `NEXT_PUBLIC_FINN_API_URL` to the deployed API URL.
- API: Render Web Service, root directory project root, build command `pip install -r requirements.txt` plus `pip install -r api/requirements.txt`, start command `uvicorn api.main:app --host 0.0.0.0 --port $PORT`. Set `FINN_PROJECT_ROOT` if the deployment layout requires it, and configure CORS `FINN_CORS_ORIGINS` to the Vercel origin.
- Add required model credentials as platform secrets only if live research is enabled. Do not place keys in Git.
- Free instances may sleep; cold-start delay must be disclosed.
- Test the public URL on a phone using mobile data, not only localhost.

Deployment has not been performed by this package. It requires the user's Vercel/Render accounts, repository permissions, environment variables, and final smoke tests.
