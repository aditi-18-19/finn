# Deployment file policy

Commit application source, configuration examples, tests, and the intended offline demo snapshot.

Do not commit:
- `.env`, `.env.local`, API keys, cookies, or credentials
- `.venv/`, `node_modules/`, `.next/`
- `data/raw/`, `data/docs/`, `data/cache/`, `data/llm_cache/`
- generated research indexes and downloaded PDFs
- local deployment inspection ZIPs and temporary reports

Before committing, inspect the exact staged filenames with:

```powershell
git diff --cached --name-only
git diff --cached --stat
```
