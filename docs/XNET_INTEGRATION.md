# X-NET integration infrastructure

Status: **experimental / disabled by default**

This branch introduces only an isolation boundary for future X-NET support. It
does not change AdminBot, UserBot, AgentBot, CustomerBot, existing Hiddify API
calls, databases, plans, node sync, subscriptions, or runtime startup.

## Safety rules

1. Existing Hiddify code remains the production path.
2. X-NET lives under `Shared/panels/xnet/` and must not be imported by current
   bot startup code until the integration passes tests.
3. X-NET failures must never stop or degrade Hiddify operations.
4. No database migration is included in this phase.
5. No X-NET credentials are stored in the repository.
6. API endpoints and capability flags remain unimplemented until verified
   against a dedicated X-NET test installation.

## Target adapter contract

Future panel implementations expose a small common contract: health check,
create/update/delete/get user, plus explicit capability flags. Features such as
subscription URLs, traffic/expiry limits and concurrent-user limits will only
be enabled when verified for that panel.

## Next test phase

After installing a disposable X-NET instance:

- verify authentication and API version;
- capture create/read/update/delete user behavior;
- verify whether caller-supplied UUIDs are preserved;
- verify traffic, expiry and concurrent/device limits;
- verify subscription output and supported protocols;
- test failure/timeout behavior;
- only then add X-NET configuration storage and AdminBot UI behind a feature
  flag.

Do not merge this branch into production until those tests pass.
