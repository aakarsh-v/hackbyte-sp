//! DevOps AI — SpacetimeDB module: log buffer + per-session runbook state.

use spacetimedb::{ReducerContext, Table};

/// Matches `LOG_BUFFER_MAX` default in the FastAPI app.
const LOG_BUFFER_MAX: usize = 2000;

#[spacetimedb::table(accessor = log_event, public)]
pub struct LogEvent {
    #[primary_key]
    #[auto_inc]
    pub id: u64,
    pub time: String,
    pub service: String,
    pub level: String,
    pub message: String,
    /// JSON-serialized `extra` or "{}".
    pub extra_json: String,
}

#[spacetimedb::table(accessor = session_runbook, public)]
pub struct SessionRunbook {
    #[primary_key]
    pub session_id: String,
    pub last_sanitized: String,
    pub last_sanitized_hash: String,
}

fn trim_old_logs(ctx: &ReducerContext) {
    let rows: Vec<_> = ctx.db.log_event().iter().collect();
    if rows.len() <= LOG_BUFFER_MAX {
        return;
    }
    let mut ids: Vec<u64> = rows.into_iter().map(|r| r.id).collect();
    ids.sort_unstable();
    let drop = ids.len() - LOG_BUFFER_MAX;
    for id in ids.into_iter().take(drop) {
        ctx.db.log_event().id().delete(id);
    }
}

#[spacetimedb::reducer]
pub fn ingest_log(
    ctx: &ReducerContext,
    time: String,
    service: String,
    level: String,
    message: String,
    extra_json: String,
) {
    ctx.db.log_event().insert(LogEvent {
        id: 0,
        time,
        service,
        level,
        message,
        extra_json,
    });
    trim_old_logs(ctx);
}

#[spacetimedb::reducer]
pub fn upsert_session_runbook(
    ctx: &ReducerContext,
    session_id: String,
    last_sanitized: String,
    last_sanitized_hash: String,
) {
    if ctx
        .db
        .session_runbook()
        .session_id()
        .find(&session_id)
        .is_some()
    {
        ctx.db.session_runbook().session_id().delete(session_id.clone());
    }
    ctx.db.session_runbook().insert(SessionRunbook {
        session_id,
        last_sanitized,
        last_sanitized_hash,
    });
}
