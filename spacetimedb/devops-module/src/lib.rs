//! DevOps AI — SpacetimeDB module: log buffer + per-session runbook history.

use spacetimedb::{ReducerContext, Table};

/// Matches `LOG_BUFFER_MAX` default in the FastAPI app.
const LOG_BUFFER_MAX: usize = 2000;

#[spacetimedb::table(name = "logs", accessor = logs, public)]
pub struct LogRow {
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

#[spacetimedb::table(
    name = "session_runbook_history",
    accessor = session_runbook_history,
    public
)]
pub struct SessionRunbookHistory {
    #[primary_key]
    #[auto_inc]
    pub id: u64,
    pub session_id: String,
    pub last_sanitized: String,
    pub last_sanitized_hash: String,
}

fn trim_old_logs(ctx: &ReducerContext) {
    let rows: Vec<_> = ctx.db.logs().iter().collect();
    if rows.len() <= LOG_BUFFER_MAX {
        return;
    }
    let mut ids: Vec<u64> = rows.into_iter().map(|r| r.id).collect();
    ids.sort_unstable();
    let drop = ids.len() - LOG_BUFFER_MAX;
    for id in ids.into_iter().take(drop) {
        ctx.db.logs().id().delete(id);
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
    ctx.db.logs().insert(LogRow {
        id: 0,
        time,
        service,
        level,
        message,
        extra_json,
    });
    trim_old_logs(ctx);
}

/// Appends one row per call. Primary key is `id` (auto-inc), not `session_id`.
#[spacetimedb::reducer]
pub fn append_session_runbook(
    ctx: &ReducerContext,
    session_id: String,
    last_sanitized: String,
    last_sanitized_hash: String,
) {
    ctx.db.session_runbook_history().insert(SessionRunbookHistory {
        id: 0,
        session_id,
        last_sanitized,
        last_sanitized_hash,
    });
}
