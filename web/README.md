# StudyHub web

React + TypeScript front end for StudyHub, built with Vite. It talks only to the backend's HTTP API
(`docs/API.md`).

```sh
npm install
npm run dev      # http://localhost:5173, proxies /api to http://127.0.0.1:8000
npm run build    # type-checks, then writes web/dist (served by the backend in production)
npm run lint     # oxlint
```

## Layout

- `src/api/`: `types.ts` mirrors `docs/API.md`; `client.ts` has the fetch helpers and the SSE chat stream reader.
- `src/lib/`: hash router, data hooks (`useApi`, `useSyncStatus`), date formatting, labels.
- `src/components/shell/`: top bar (search, sync status) and class sidebar.
- `src/components/course/`: course header and tabs (timeline, assignments, files, notes, recordings, announcements).
- `src/components/viewer/`: PDF, transcript, markdown, and assignment viewers.
- `src/components/chat/`: chat panel, message rendering with citation chips, composer.
- `src/styles.css`: design tokens (light and dark) and all styles.

Routes live in the URL hash so reloads keep your place: `#/course/3/timeline`, `#/resource/42?page=27`,
`#/resource/17?t=2472`, `#/assignment/7?q=2`.
