# OHDSI Study Agent — Interactive Demonstration

This independently deployable FastAPI application places a review-gated browser controller in front of StudyAgent ACP. It does not execute cohorts, databases, Atlas changes, code, or phenotype-improvement patches.

## Workflow mapping

| R-shell checkpoint | Chat controller state/action | ACP flow |
|---|---|---|
| intent split and role navigation | `study_intent`, confirmed role statements, active role | `phenotype_intent_split` |
| phenotype selection with namespaced source identity | `selected_phenotypes[role]` | `phenotype_recommendation` |
| make-computable clarification and scope checkpoint | immutable confirmed scope | `phenotype_make_computable` (`confirmed_scope:false`, then `required`) |
| frozen CSV/manifest review | session-private package plus exact policy SHA-256 | review URLs + deterministic helper |
| exact human approval | `approval_record` bound to scope/review/policy | `provided_only` only after approval |
| technical artifact handoff | private Capr/Circe downloads | `phenotype_make_computable` |

The controller vendors the deterministic policy rules from AgentPlayGround's `phenotype_review_csv_to_concept_sets.py` and its Atlas concept-set normalization rules. It intentionally rejects a full Circe cohort definition as an Atlas concept-set import. Current ACP accepts 1–500 candidates for deterministic `required` review; LLM `propose` is separately limited to 100. This app uses only `required` review. The prepared conference ACP also supports `scope_draft: true`; this app treats its result as advisory clarification and never promotes it to confirmed scope.

## Revision semantics

Confirming revised cohort statements invalidates all downstream selections, scope, frozen-review references, exact policies, approvals, and currently presented artifact references. Selecting a different phenotype for one role or starting a new make-computable narrative preserves selections for other roles, but invalidates the dependent scope/review/approval chain. Existing private files are not deleted by a revision; they are simply no longer presented as current artifacts. Expired controller sessions also remove their associated ChatKit threads and attachments when expiry cleanup next runs.

## Local development

```bash
cd conference/chatkit
uv sync --extra test
cp .env.example .env  # set OPENAI_API_KEY only when enabling ChatKit/Agents dialogue
set -a; . ./.env; set +a
uv run uvicorn study_chat.app:app --host 127.0.0.1 --port 8795 --root-path "$APP_BASE_PATH"
```

Open `http://127.0.0.1:8795/study-chat/`. The deterministic browser controls are available immediately. The self-hosted ChatKit backend is exposed at `POST /study-chat/chatkit`; it requires the opaque `X-Study-Session` header and persists threads in the same private SQLite service store. The browser sends only this opaque session ID to the backend. The deployed ChatKit web component requires `CHATKIT_DOMAIN_KEY`, a public domain-registration key, and uses a custom `fetch` hook to attach the session ID. OpenAI recommends a self-hosted ChatKit server plus Agents SDK with a durable Store and server-side context for new work. See [advanced ChatKit documentation](https://developers.openai.com/api/docs/guides/custom-chatkit).

## Deployment

Install `uv` for the service user, then run `uv sync --no-dev` from `/opt/study-chat/conference/chatkit`. Create a protected `/etc/study-chat.env` (mode `0640`, owned by the service user) from `.env.example`; make `SESSION_DB` and `ARTIFACT_ROOT` persistent, private storage. `SESSION_TTL_HOURS` defaults to 24; on creating a session, expired session rows and only their matching private artifact directories are removed. Configure `STUDY_AGENT_URL=https://atlas.napdi.org/StudyAgent`, `APP_BASE_PATH=/study-chat`, and bind only `127.0.0.1:8795`.

`/etc/systemd/system/study-chat.service`:

```ini
[Unit]
Description=OHDSI Study Chat conference application
After=network.target
[Service]
User=study-chat
Group=study-chat
WorkingDirectory=/opt/study-chat/conference/chatkit
EnvironmentFile=/etc/study-chat.env
ExecStart=/usr/local/bin/uv run --no-sync uvicorn study_chat.app:app --host ${APP_HOST} --port ${APP_PORT} --proxy-headers --root-path ${APP_BASE_PATH}
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/study-chat
[Install]
WantedBy=multi-user.target
```

Apache location example (do not apply directly in production):

```apache
ProxyPreserveHost On
ProxyPass        /study-chat/ http://127.0.0.1:8795/ retry=0 timeout=60
ProxyPassReverse /study-chat/ http://127.0.0.1:8795/
RequestHeader set X-Forwarded-Proto "https"
ProxyTimeout 60
```

For a ChatKit streaming endpoint, disable proxy buffering and set a longer timeout for that endpoint. Run `systemctl disable --now study-chat` after the conference, then archive or securely remove `/var/lib/study-chat` according to the event retention policy.

## Safety and limits

The server keeps sessions/artifacts private per opaque session ID, validates review policies deterministically, limits review uploads, pins ACP review downloads to the configured ACP origin, and never lets browser input choose server paths or URLs. Configure web-server rate limiting, session expiry cleanup, bounded worker concurrency, request-body limits, and OpenAI project usage caps. No API key is sent to the browser; raw patient-level data/PHI must not be entered.

Phenotype-improvement advice is not exposed as an apply operation. The current ACP requires a protocol/study-intent string and one cohort record; the controller sends that contract and stores returned advice only. Any advice must be labeled unvalidated and manually implemented and validated in [Atlas3](https://atlas.napdi.org/Atlas3) after downloading the unchanged Circe JSON.
