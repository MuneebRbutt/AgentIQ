# AgentIQ frontend

Operator dashboard (React 19 + TypeScript + Vite + Tailwind). Setup and how to use it are in
the [root README](../README.md); conventions in [CLAUDE.md](../CLAUDE.md) and
[ADR-0008](../docs/adr/0008-operator-dashboard.md).

```powershell
npm ci
npm run dev        # http://localhost:5173 (reads VITE_API_URL from the root .env)
npm run lint ; npm run typecheck ; npm test ; npm run build
npm run gen:api    # after the API changes: regenerate src/api/schema.d.ts
```

```
src/
├── api/          typed client (client.ts, generated schema.d.ts), login state (auth*.ts*)
├── components/   shared UI (ui.tsx), page frame (Layout), editor/ (profile form)
├── lib/          plain helpers: paths, validation messages, formatting, status colours
├── pages/        one file per screen; profile/ holds the tabs of one profile
└── test/         Vitest setup, a fake API (fakeApi.ts) and renderApp for page tests
```
