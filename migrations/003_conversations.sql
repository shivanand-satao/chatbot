-- Prepared only. Apply explicitly after reviewing runtime roles and policies.
-- chatbot schema is created by migration 001. No public/auth tables are changed.
BEGIN;
CREATE TABLE chatbot.conversations (
    id uuid PRIMARY KEY,
    owner_kind text NOT NULL CHECK (owner_kind IN ('user','admin')),
    owner_id bigint NOT NULL CHECK (owner_id > 0),
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    next_sequence bigint NOT NULL DEFAULT 1 CHECK (next_sequence > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_owner_cursor ON chatbot.conversations
    (owner_kind,owner_id,created_at DESC,id DESC);
CREATE TABLE chatbot.conversation_requests (
    conversation_id uuid NOT NULL REFERENCES chatbot.conversations(id) ON DELETE CASCADE,
    request_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('pending','complete','failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id,request_id)
);
CREATE UNIQUE INDEX one_pending_request_per_conversation
    ON chatbot.conversation_requests(conversation_id) WHERE state='pending';
CREATE TABLE chatbot.messages (
    conversation_id uuid NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    request_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('user','assistant')),
    content text NOT NULL CHECK (length(btrim(content)) BETWEEN 1 AND 20000),
    status text NOT NULL CHECK (status IN ('complete','failed')),
    sources jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(sources)='array' AND octet_length(sources::text)<=16000),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id,sequence),
    UNIQUE (conversation_id,request_id,role),
    FOREIGN KEY (conversation_id,request_id)
        REFERENCES chatbot.conversation_requests(conversation_id,request_id) ON DELETE CASCADE,
    CHECK (role<>'user' OR (status='complete' AND sources='[]'::jsonb)),
    CHECK (status<>'failed' OR sources='[]'::jsonb)
);
-- Fail closed for client roles. Runtime service policies/grants are deliberately
-- NOT created here; ownership predicates in the repository are an additional layer.
ALTER TABLE chatbot.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE chatbot.conversation_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE chatbot.messages ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON chatbot.conversations,chatbot.conversation_requests,chatbot.messages FROM PUBLIC;
COMMIT;