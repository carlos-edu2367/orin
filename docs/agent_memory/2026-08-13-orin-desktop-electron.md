# Orin Desktop Electron v1

The first desktop layer keeps the Python `Supervisor` as the only authority for
Docker, datastores, migrations, API, publisher, workers, health checks and
shutdown. `orin --desktop` starts the Electron host early; Electron never
duplicates launcher process logic.

The contract between them is an atomic `desktop-startup.json` snapshot in the
configured run directory. It contains only human-safe state, the local loopback
URL, log directory, cooperative shutdown request path and the fixed launcher
retry command. The splash polls it and loads the existing API-served frontend
only when the supervisor writes `mode: ready` after `/healthz`, `/readyz`, worker
heartbeats and frontend probing succeed.

Electron uses `contextIsolation: true`, `nodeIntegration: false`, a narrow
`contextBridge`, localhost-only navigation and Electron's single-instance lock.
Closing it writes the existing stop request; it never kills Docker Desktop.
The package configuration in `desktop/package.json` builds the Electron host,
but a future distribution must package the frozen Orin launcher alongside it.
