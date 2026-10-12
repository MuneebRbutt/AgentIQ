# Terminal 01: PostgreSQL
Set-Location F:\FYP\agentiq
docker compose up -d db

# Terminal 02: backend
Set-Location F:\FYP\agentiq\backend
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
fastapi dev app\main.py

# Terminal 03: Frontend
Set-Location F:\FYP\agentiq\frontend
npm run dev

# To stop postgre without deleting itts data.
Set-Location F:\FYP\agentiq
docker compose stop