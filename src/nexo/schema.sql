-- F1 schema: channel, transcript and memory import.
-- Vector column arrives in F7 (ADR 0003); F1 only needs text.

CREATE TABLE IF NOT EXISTS profiles (
  id          text PRIMARY KEY,
  name        text UNIQUE NOT NULL,
  system_prompt text NOT NULL DEFAULT '',
  default_model text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS channels (
  id          text PRIMARY KEY,
  kind        text NOT NULL,
  external_id text NOT NULL,          -- e.g. the Telegram chat id to allowlist
  profile_id  text NOT NULL REFERENCES profiles(id),
  settings    jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {"token_env": "..."} for a dedicated bot
  enabled     boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, external_id)
);

ALTER TABLE channels ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}'::jsonb;

-- A model chain was prototyped and dropped the same day (2026-11-25): one model per profile, and
-- the user decides when to change it. `default_model` is the single source, as `provider/model`.
ALTER TABLE profiles DROP COLUMN IF EXISTS models;

CREATE TABLE IF NOT EXISTS conversations (
  id          text PRIMARY KEY,
  profile_id  text NOT NULL REFERENCES profiles(id),
  channel_id  text REFERENCES channels(id),
  title       text NOT NULL DEFAULT '',
  engine_session_id text,
  context_epoch int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_turn_at timestamptz
);

CREATE TABLE IF NOT EXISTS turns (
  id          text PRIMARY KEY,
  conversation_id text NOT NULL REFERENCES conversations(id),
  seq         int NOT NULL,
  state       text NOT NULL,
  kind        text NOT NULL DEFAULT 'message',
  idempotency_key text UNIQUE NOT NULL,
  input_text  text NOT NULL,
  placeholder_message_id bigint,
  partial_text text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  started_at  timestamptz,
  ended_at    timestamptz,
  locked_until timestamptz,
  error_class text,
  UNIQUE (conversation_id, seq)
);

-- F4 columns added in place: the schema is applied with IF NOT EXISTS, so keep them idempotent.
ALTER TABLE turns ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'message';
ALTER TABLE turns ADD COLUMN IF NOT EXISTS placeholder_message_id bigint;
ALTER TABLE turns ADD COLUMN IF NOT EXISTS partial_text text;
ALTER TABLE turns ADD COLUMN IF NOT EXISTS locked_until timestamptz;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS model_override text;
-- Per-conversation model bookkeeping is gone with the chain: the session is reset whenever the
-- model changes, so there is nothing to reconcile (2026-11-25).
ALTER TABLE conversations DROP COLUMN IF EXISTS engine_model;
ALTER TABLE conversations DROP COLUMN IF EXISTS model_retry_at;

CREATE INDEX IF NOT EXISTS turns_queued ON turns (created_at) WHERE state = 'queued';

CREATE TABLE IF NOT EXISTS messages (
  id          bigserial PRIMARY KEY,
  conversation_id text NOT NULL REFERENCES conversations(id),
  turn_id     text REFERENCES turns(id),
  role        text NOT NULL,
  content     text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_conversation_created
  ON messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS memory_items (
  id          bigserial PRIMARY KEY,
  profile_id  text NOT NULL REFERENCES profiles(id),
  kind        text NOT NULL DEFAULT 'fact',
  content     text NOT NULL,
  source      text NOT NULL DEFAULT 'import',
  created_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz
);

CREATE INDEX IF NOT EXISTS memory_profile_created ON memory_items (profile_id, created_at DESC);

-- F7: vectors live beside the facts, never instead of them. The model name is part of the key so a
-- change of embedding model is a re-index, not a migration.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_vectors (
  memory_id   bigint NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
  model       text NOT NULL,
  embedding   vector(1024) NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (memory_id, model)
);

CREATE INDEX IF NOT EXISTS memory_vectors_hnsw
  ON memory_vectors USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS kv (
  key         text PRIMARY KEY,
  value       text NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- F5: the skills registry is an index of the git repo, never the source of truth.
CREATE TABLE IF NOT EXISTS skills (
  slug        text PRIMARY KEY,
  category    text NOT NULL,
  path        text NOT NULL,
  description text NOT NULL,
  frontmatter jsonb NOT NULL DEFAULT '{}'::jsonb,
  content_hash text NOT NULL,
  body        text NOT NULL,
  profiles    text[] NOT NULL DEFAULT '{}',   -- empty = every profile
  enabled     boolean NOT NULL DEFAULT true,
  synced_at   timestamptz NOT NULL DEFAULT now()
);

-- F2: jobs. `kind=script` runs a command; `kind=prompt` enqueues an agent turn (ADR 0013).
-- `owner` makes the cut a transaction instead of a list of manual steps (ADR 0012).
CREATE TABLE IF NOT EXISTS jobs (
  id          text PRIMARY KEY,
  kind        text NOT NULL CHECK (kind IN ('script', 'prompt')),
  name        text UNIQUE NOT NULL,
  profile_id  text REFERENCES profiles(id),
  channel_id  text REFERENCES channels(id),
  schedule    text NOT NULL,              -- 5-field cron, minute resolution
  command     text,                       -- kind=script
  payload     text,                       -- kind=prompt: the prompt text
  owner       text NOT NULL DEFAULT 'nexo' CHECK (owner IN ('nexo', 'hermes')),
  enabled     boolean NOT NULL DEFAULT true,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  state       text NOT NULL DEFAULT 'queued',
  attempts    smallint NOT NULL DEFAULT 0,
  max_attempts smallint NOT NULL DEFAULT 3,
  locked_until timestamptz,
  last_error  text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobs_due ON jobs (next_run_at) WHERE state = 'queued' AND enabled;

CREATE TABLE IF NOT EXISTS job_runs (
  id          bigserial PRIMARY KEY,
  job_id      text NOT NULL REFERENCES jobs(id),
  scheduled_for timestamptz NOT NULL,
  started_at  timestamptz NOT NULL DEFAULT now(),
  ended_at    timestamptz,
  status      text NOT NULL DEFAULT 'running',
  exit_code   int,
  output      text,
  error       text,
  UNIQUE (job_id, scheduled_for)
);

-- Cost ledger. One row per completed turn: what the engine reported, not an estimate.
-- Deliberately no cache columns: the adapter does not measure them yet, and a column that is
-- always null reads like a measurement. Subagent tokens are not reported by the engine and
-- therefore are NOT in here (declared in operations.md, not hidden).
CREATE TABLE IF NOT EXISTS model_calls (
  id           bigserial PRIMARY KEY,
  turn_id      text NOT NULL,
  profile_id   text,
  model_id     text,
  tokens_input int  NOT NULL DEFAULT 0,
  tokens_output int NOT NULL DEFAULT 0,
  cost_usd     numeric(12,6) NOT NULL DEFAULT 0,
  latency_ms   int  NOT NULL DEFAULT 0,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS model_calls_created ON model_calls (created_at);
CREATE INDEX IF NOT EXISTS model_calls_profile ON model_calls (profile_id, created_at);

-- One running turn per conversation. The queue is serial per chat by design (order matters);
-- a partial unique index makes that a rule of the database instead of a hope about workers.
CREATE UNIQUE INDEX IF NOT EXISTS turns_one_running ON turns (conversation_id) WHERE state = 'running';
