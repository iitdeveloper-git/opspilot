# OpsPilot 2.0 Web Command Center API Reference

All mutation endpoints (`POST`, `PUT`, `DELETE`) require active session authentication via HTTP-only cookie and the `X-CSRF-Token` header.

## Authentication Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Authenticate with administrator password |
| `POST` | `/api/auth/logout` | Terminate session and clear cookie |
| `GET` | `/api/auth/me` | Retrieve current authenticated user & CSRF token |

## Monitoring & Fleet Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/overview` | System CPU, memory, load average, and count summaries |
| `GET` | `/api/containers` | List all Docker containers with status, ports, and snooze state |
| `POST` | `/api/containers/{name}/restart` | Safely restart a specific container |
| `GET` | `/api/containers/{name}/logs` | Retrieve tail logs (last 100 lines) of a container |
| `POST` | `/api/containers/{name}/snooze` | Snooze alerts for a container (1h, 4h, 24h, 7d, indefinite) |
| `POST` | `/api/containers/{name}/unsnooze`| Unmute alerts for a container |

## Probes & Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/probes` | List all HTTP/HTTPS endpoint probes with live latency and SSL |
| `POST` | `/api/probes` | Register a new endpoint probe target |
| `POST` | `/api/probes/{id}/toggle` | Enable or disable a probe target |
| `DELETE` | `/api/probes/{id}` | Remove a probe target |

## Renewals & Billing

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/renewals` | List tracked services, domains, and certificates with renewal dates |
| `POST` | `/api/renewals` | Register a new renewal item |
| `PUT` | `/api/renewals/{id}` | Update renewal item amount, dates, or details |
| `POST` | `/api/renewals/{id}/pay` | Mark renewal item as paid and roll over cycle |
| `POST` | `/api/renewals/{id}/snooze` | Snooze renewal alert notifications |
| `DELETE` | `/api/renewals/{id}` | Delete a tracked renewal item |

## Incidents & Alerts

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/incidents` | Query active and resolved incident feed |
| `POST` | `/api/incidents/{id}/resolve` | Mark an incident as resolved with audit timestamp |

## Alert Routing

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/alert-routes` | List configured alert routing rules |
| `POST` | `/api/alert-routes` | Create a new alert routing rule |
| `PUT` | `/api/alert-routes/{id}` | Update an existing alert routing rule |
| `DELETE` | `/api/alert-routes/{id}` | Remove an alert routing rule |
| `POST` | `/api/alert-routes/test` | Dispatch a synthetic test notification through a route |
