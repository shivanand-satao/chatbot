-- Explicitly applied service role. Credentials are provisioned separately.
BEGIN;
CREATE ROLE chatbot_history_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA chatbot TO chatbot_history_runtime;
GRANT SELECT,INSERT,UPDATE,DELETE ON chatbot.conversations,
    chatbot.conversation_requests,chatbot.messages TO chatbot_history_runtime;
REVOKE ALL ON chatbot.conversations,chatbot.conversation_requests,chatbot.messages
    FROM anon,authenticated,service_role;
ALTER TABLE chatbot.conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE chatbot.conversation_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE chatbot.messages FORCE ROW LEVEL SECURITY;
CREATE POLICY history_owner ON chatbot.conversations TO chatbot_history_runtime
    USING (owner_kind=current_setting('app.history_owner_kind',true)
       AND owner_id::text=current_setting('app.history_owner_id',true))
    WITH CHECK (owner_kind=current_setting('app.history_owner_kind',true)
       AND owner_id::text=current_setting('app.history_owner_id',true));
CREATE POLICY history_request_owner ON chatbot.conversation_requests TO chatbot_history_runtime
    USING (EXISTS (SELECT 1 FROM chatbot.conversations c WHERE c.id=conversation_id))
    WITH CHECK (EXISTS (SELECT 1 FROM chatbot.conversations c WHERE c.id=conversation_id));
CREATE POLICY history_message_owner ON chatbot.messages TO chatbot_history_runtime
    USING (EXISTS (SELECT 1 FROM chatbot.conversations c WHERE c.id=conversation_id))
    WITH CHECK (EXISTS (SELECT 1 FROM chatbot.conversations c WHERE c.id=conversation_id));
ALTER ROLE chatbot_history_runtime SET statement_timeout='10s';
ALTER ROLE chatbot_history_runtime SET lock_timeout='3s';
ALTER ROLE chatbot_history_runtime SET idle_in_transaction_session_timeout='15s';
ALTER ROLE chatbot_history_runtime SET search_path=pg_catalog,chatbot;
COMMIT;