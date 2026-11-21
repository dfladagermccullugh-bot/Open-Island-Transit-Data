-- Applied 2026-11-19 between 0002 and 0003 and saved here afterwards.
-- 0001_collector.sql already creates pg_net in the "extensions" schema, so a
-- fresh setup does not need this file; it records what ran on the live project.

-- pg_net was created in the public schema; the Supabase linter asks for
-- extensions to live elsewhere. pg_net cannot be moved, so recreate it.
-- Its functions stay in the "net" schema either way.
drop extension if exists pg_net;
create extension pg_net with schema extensions;
