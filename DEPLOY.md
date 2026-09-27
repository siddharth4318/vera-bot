# Deploy on Render (about 10 minutes)

1. **Put the code on GitHub.** Create a new **public** repo (e.g. `vera-bot`) and upload the contents of this folder, so that `app.py` is at the repo root.
2. **Create the service.** In Render, go to **New → Blueprint**, pick the repo, and Render reads `render.yaml`. Alternatively, go to **New → Web Service** and set:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1`
   - Health check path: `/v1/healthz`
3. **Check the live URL.** Once it's live, open `https://<your-app>.onrender.com/v1/healthz`. You should see `{"status":"ok",...}`.
4. **Run the official simulator against it.** In `judge_simulator.py`, set `BOT_URL` to your Render URL and set your LLM key. Then run `python judge_simulator.py`.
5. **Submit.** Submit the base URL, e.g. `https://vera-bot.onrender.com` (no `/v1`).

## Important: keep it awake

- **The free plan sleeps.** Render's free web services spin down after about 15 minutes idle. Waking up can take longer than the judge's 30 s timeout.
- **Sleeping also wipes the in-memory context.** If the bot restarts, the judge's pushed context is gone.

For the evaluation window, do one of these:

- **Best:** switch the service to a paid always-on instance type while you're being judged.
- **Free:** add a monitor on UptimeRobot or cron-job.org that GETs `/v1/healthz` every 5 minutes.

Always keep `--workers 1`. State lives in process memory, so two workers would split it.

## Optional: turn on the LLM polish layer

In Render → Environment, add:

- `VERA_LLM_PROVIDER` = `anthropic` | `openai` | `gemini` | `groq` | `openrouter` | `deepseek`
- `VERA_LLM_API_KEY` = your key
- `VERA_LLM_MODEL` (optional)

The polish layer can only rephrase. Any output that adds or drops a number, adds a URL or a taboo word, or loses the CTA is discarded, and the deterministic draft is sent instead.
