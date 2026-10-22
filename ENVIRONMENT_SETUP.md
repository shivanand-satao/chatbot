# Complete runtime environment template

Use D:\cc\chatbot\.env.example as the planned deployment configuration checklist.
It includes gateway authentication, separate history/retrieval database access,
optional Groq access, safe feature flags, and CPU-only settings. It contains no
real credentials. The existing D:\cc\chatbot\.env has not been read, copied,
overwritten, or rotated by this change.

## Fill privately

1. Rotate database credentials exposed in the screenshot, updating affected
   applications as needed. Do not reuse the exposed history password.
2. Generate a strong service secret and configure the same value on the backend.
3. Replace the history connection placeholder with the complete restricted DSN.
4. Provision a separate read-only retrieval role before filling retrieval_db_url.
   Never substitute the privileged postgres/indexing connection.
5. Fill groq_api privately if you plan to use Groq later. Optional placeholder
   values must not be treated as valid credentials by future configuration code.
6. Leave all feature flags false until the corresponding deployment checks pass.

You can enter all available values together, but no template can guarantee that
you will never need an update: credential rotation, missing role provisioning,
and future feature enablement still require configuration changes. Keep disabled
optional placeholders until their values are ready; do not enable those features.

## Implementation status — not a deployable configuration yet

Existing tooling uses groq_api and db_url from a local .env; the history launcher
uses history_db_url from that file. It does not yet read Render's process variables.
BACKEND_ORIGIN, CHATBOT_SERVICE_SECRET, retrieval_db_url and the feature flags are
planned configuration, not implemented runtime controls. No security protection
is established simply by adding a secret variable. CPU environment settings do
not provision the embedding model cache.

Before public deployment, implement and test a production entry point that reads
the environment, validates enabled-feature credentials, fails closed on missing or
placeholder service secrets, authenticates backend requests, and binds the assigned
port. The backend gateway must also be implemented. No production start command or
requirements file has been created by this template-only change.

Do not import your entire existing local .env into the public service. Do not
commit a populated secrets file. No migrations, database writes, account changes,
generation, persistence enablement, pushes, or deployments were performed here.