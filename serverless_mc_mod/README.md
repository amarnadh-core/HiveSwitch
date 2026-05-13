# Serverless MC Helper Mod

Fabric client mod for the Serverless MC prototype.

The mod polls the local helper API, falls back to the shared `session.json`
file when the helper API is unavailable, redirects local server entries to the
currently active host, and shows a migration screen while a host handoff is in
progress.

Build with:

```powershell
.\gradlew.bat build
```
