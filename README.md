# quantix / DEMON CORE (Render deployment notes)

Ye repository ek Pyrogram-based Telegram bot/service hai (main.py). Maine repository ko Render ya kisi aur PaaS par "web service" ke roop me chalane ke liye ready kar diya hai taaki service HTTP health checks respond kare aur easier 24/7 availability mil sake.

Quick summary of changes applied:
- Added embedded aiohttp health endpoint in `main.py` at GET /health (returns "OK"). Binds to environment PORT (Render sets this automatically).
- Added `aiohttp` to `requirements.txt` so dependency install ho jaye.

How to run locally
1. Create a virtualenv and install deps:
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

2. Export required environment variables (example):
   export API_ID=YOUR_API_ID
   export API_HASH=YOUR_API_HASH
   export BOT_TOKEN=YOUR_BOT_TOKEN
   export SESSION_STRING_1=""
   export SESSION_STRING_2=""
   export TARGET_CHAT_ID=-1001234567890
   export PORT=8000

3. Start the app:
   python main.py

4. Health check:
   curl http://localhost:8000/health
   # should print: OK

Deploying to Render (recommended)
1. Create a new Web Service in Render dashboard and connect this GitHub repo.
2. Set the build command to: `pip install -r requirements.txt`
   (Render will run this automatically if it detects Python but adding it is safe.)
3. Start command: `web: python main.py` (Procfile already contains this)
4. Add environment variables in Render (API_ID, API_HASH, BOT_TOKEN, SESSION_STRING_1/2, TARGET_CHAT_ID, etc.).
5. Deploy. After deploy, Render will assign a URL. Visit `https://<your-render-service>.onrender.com/health` — you should see `OK`.

Keeping the service online 24/7
- Free Render instances can still sleep after inactivity. To keep reliably online:
  - Option A (recommended): Enable Render "Always On" feature (paid) for the service.
  - Option B: Use an external uptime monitor (UptimeRobot, Pingdom) to ping `https://<your-render-service>.onrender.com/health` every 5 minutes.

GitHub Actions: manual health check
- There's a workflow `Check service health` you can trigger manually from the Actions tab to test your deployed health URL (it accepts the URL as input).

If you want, I can also add an UptimeRobot monitor for you (I cannot create it from here because it requires your UptimeRobot credentials). See `UPTIME_MONITOR_SETUP.md` for step-by-step instructions.

---

If you want me to also add a short README in Hindi or translate any piece, tell me. If everything is fine, deploy to Render and then provide the deployed URL so I can suggest the exact uptime monitor configuration (interval, expected response etc.).
