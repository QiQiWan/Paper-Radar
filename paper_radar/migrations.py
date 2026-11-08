# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Versioned, append-only SQLite migrations with tamper detection.

Each database kind has a separate ordered migration registry. A migration is
executed inside BEGIN IMMEDIATE; its version and deterministic checksum are
committed with the DDL. No silent downgrade of a newer database is permitted.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import sqlite3


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return sha256((self.name+'\n'+'\n'.join(self.statements)).encode('utf-8')).hexdigest()


REGISTRIES: dict[str, tuple[Migration, ...]] = {
    'research': (
        Migration(1, 'baseline_v115', (
            'CREATE INDEX IF NOT EXISTS idx_topic_papers_ref ON topic_papers(topic_id,paper_id)',
        )),
        Migration(2, 'synthesis_history', (
            '''CREATE TABLE IF NOT EXISTS synthesis_runs (
                id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, generated_at TEXT NOT NULL,
                paper_count INTEGER NOT NULL, finding_count INTEGER NOT NULL,
                status TEXT NOT NULL, output_name TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_synthesis_topic ON synthesis_runs(topic_id,generated_at)',
        )),
        Migration(3, 'local_human_reading_notes', (
            '''CREATE TABLE IF NOT EXISTS local_paper_notes (
                topic_id TEXT NOT NULL,paper_id TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',
                next_action TEXT NOT NULL DEFAULT 'none'
                  CHECK(next_action IN ('none','read','replicate','cite','discuss')),
                revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),updated_at TEXT NOT NULL,
                PRIMARY KEY(topic_id,paper_id),
                FOREIGN KEY(topic_id,paper_id) REFERENCES topic_papers(topic_id,paper_id)
                  ON DELETE CASCADE)''',
            'CREATE INDEX IF NOT EXISTS idx_local_paper_notes_action ON local_paper_notes(topic_id,next_action,paper_id)',
        )),
        Migration(4, 'research_claims_and_pdf_anchors', (
            '''CREATE TABLE IF NOT EXISTS research_anchors (
                id TEXT PRIMARY KEY,topic_id TEXT NOT NULL,paper_id TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL,page INTEGER NOT NULL CHECK(page BETWEEN 1 AND 120),
                quote TEXT NOT NULL,quote_start INTEGER NOT NULL,quote_end INTEGER NOT NULL,
                chunk_id INTEGER NOT NULL DEFAULT 0,experiment_id TEXT NOT NULL DEFAULT '',
                note_revision INTEGER NOT NULL DEFAULT 0,
                note_snapshot TEXT NOT NULL DEFAULT '',
                note_action_snapshot TEXT NOT NULL DEFAULT 'none',created_at TEXT NOT NULL,
                FOREIGN KEY(topic_id,paper_id) REFERENCES topic_papers(topic_id,paper_id) ON DELETE CASCADE)''',
            'CREATE INDEX IF NOT EXISTS idx_anchors_topic_paper ON research_anchors(topic_id,paper_id,created_at)',
            '''CREATE TABLE IF NOT EXISTS research_claims (
                id TEXT PRIMARY KEY,topic_id TEXT NOT NULL,body TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('finding','comparison','limitation','next_step')),
                decision TEXT NOT NULL DEFAULT 'draft' CHECK(decision IN ('draft','confirmed')),
                revision INTEGER NOT NULL CHECK(revision>=1),review_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_claims_topic ON research_claims(topic_id,updated_at DESC)',
            '''CREATE TABLE IF NOT EXISTS research_claim_links (
                claim_id TEXT NOT NULL,anchor_id TEXT NOT NULL,topic_id TEXT NOT NULL,
                PRIMARY KEY(claim_id,anchor_id),
                FOREIGN KEY(claim_id) REFERENCES research_claims(id) ON DELETE CASCADE,
                FOREIGN KEY(anchor_id) REFERENCES research_anchors(id) ON DELETE CASCADE)''',
            'CREATE INDEX IF NOT EXISTS idx_claim_links_anchor ON research_claim_links(anchor_id,claim_id)',
            '''CREATE TABLE IF NOT EXISTS research_claim_audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,topic_id TEXT NOT NULL,claim_id TEXT NOT NULL,
                revision INTEGER NOT NULL,decision TEXT NOT NULL,body TEXT NOT NULL,
                anchors_json TEXT NOT NULL,review_note TEXT NOT NULL,event_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_claim_audit_topic ON research_claim_audit(topic_id,claim_id,seq)',
            '''CREATE TRIGGER IF NOT EXISTS research_claim_audit_no_update
                BEFORE UPDATE ON research_claim_audit BEGIN
                SELECT RAISE(ABORT, 'research audit is immutable'); END''',
            '''CREATE TRIGGER IF NOT EXISTS research_claim_audit_no_delete
                BEFORE DELETE ON research_claim_audit BEGIN
                SELECT RAISE(ABORT, 'research audit is immutable'); END''',
        )),
        Migration(5, 'follow_tracking_and_outbox', (
            '''CREATE TABLE IF NOT EXISTS following_preferences (
                topic_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
                notify_new INTEGER NOT NULL DEFAULT 1,
                notify_updated INTEGER NOT NULL DEFAULT 1,
                email_enabled INTEGER NOT NULL DEFAULT 0,
                email_to TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS following_checkpoints (
                topic_id TEXT NOT NULL, source_name TEXT NOT NULL,
                query_hash TEXT NOT NULL, last_success TEXT NOT NULL DEFAULT '',
                last_attempt TEXT NOT NULL DEFAULT '', window_start TEXT NOT NULL DEFAULT '',
                window_end TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
                error_code TEXT NOT NULL DEFAULT '', fail_count INTEGER NOT NULL DEFAULT 0,
                result_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(topic_id,source_name))''',
            '''CREATE TABLE IF NOT EXISTS following_observations (
                topic_id TEXT NOT NULL, source_name TEXT NOT NULL,
                source_id TEXT NOT NULL, paper_id TEXT NOT NULL,
                signature TEXT NOT NULL, source_updated TEXT NOT NULL DEFAULT '',
                doi TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                published TEXT NOT NULL DEFAULT '', venue TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL,
                PRIMARY KEY(topic_id,source_name,source_id))''',
            'CREATE INDEX IF NOT EXISTS idx_follow_observed ON following_observations(topic_id,source_name,observed_at)',
            '''CREATE TABLE IF NOT EXISTS following_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL,
                source_name TEXT NOT NULL, source_id TEXT NOT NULL,
                paper_id TEXT NOT NULL, signature TEXT NOT NULL,
                source_updated TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                UNIQUE(topic_id,source_name,source_id,signature))''',
            '''CREATE TABLE IF NOT EXISTS following_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL,
                source_name TEXT NOT NULL, source_id TEXT NOT NULL,
                paper_id TEXT NOT NULL, kind TEXT NOT NULL,
                signature TEXT NOT NULL, observed_at TEXT NOT NULL,
                title TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(topic_id,source_name,source_id,kind,signature))''',
            'CREATE INDEX IF NOT EXISTS idx_follow_events_topic ON following_events(topic_id,id DESC)',
            '''CREATE TABLE IF NOT EXISTS following_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL,
                event_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL, seen_at TEXT NOT NULL DEFAULT '',
                email_sent_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(event_id) REFERENCES following_events(id))''',
            'CREATE INDEX IF NOT EXISTS idx_follow_unread ON following_notifications(topic_id,seen_at,id DESC)',
            '''CREATE TABLE IF NOT EXISTS following_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL,
                started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
                status TEXT NOT NULL, source_json TEXT NOT NULL,
                discovered INTEGER NOT NULL DEFAULT 0, events_created INTEGER NOT NULL DEFAULT 0)''',
            'CREATE INDEX IF NOT EXISTS idx_follow_runs ON following_runs(topic_id,id DESC)',
        )),
        Migration(6, 'research_inquiry_snapshots_v140', (
            '''CREATE TABLE IF NOT EXISTS research_inquiries (
                id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, question TEXT NOT NULL,
                created_at TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                snapshot_json TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_inquiries_topic ON research_inquiries(topic_id,created_at DESC,id DESC)',
            '''CREATE TRIGGER IF NOT EXISTS inquiry_no_update BEFORE UPDATE ON research_inquiries
                BEGIN SELECT RAISE(ABORT, 'inquiry snapshot is immutable'); END''',
            '''CREATE TRIGGER IF NOT EXISTS inquiry_no_delete BEFORE DELETE ON research_inquiries
                BEGIN SELECT RAISE(ABORT, 'inquiry snapshot is immutable'); END''',
        )),
    ),
    'portal_auth': (
        Migration(1, 'accounts_and_sessions', (
            '''CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','user')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                csrf TEXT NOT NULL, created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, last_seen INTEGER NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(id))''',
            'CREATE INDEX IF NOT EXISTS idx_session_user ON sessions(account_id,expires_at)',
            '''CREATE TABLE IF NOT EXISTS auth_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, identifier TEXT NOT NULL,
                occurred_at INTEGER NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_attempts_key ON auth_attempts(identifier,occurred_at)',
        )),
        Migration(2, 'case_insensitive_accounts', (
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_username_nocase ON accounts(username COLLATE NOCASE)',
        )),
        Migration(3, 'persistent_search_queue', (
            '''CREATE TABLE IF NOT EXISTS portal_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('queued','running','success','failed','cancelled')),
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 2,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error TEXT NOT NULL DEFAULT '',
                worker_id TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id))''',
            'CREATE INDEX IF NOT EXISTS idx_jobs_account ON portal_jobs(account_id,id DESC)',
            'CREATE INDEX IF NOT EXISTS idx_jobs_state ON portal_jobs(state,id)',
            '''CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_search_per_account
                ON portal_jobs(account_id) WHERE state IN ('queued','running')''',
        )),
        Migration(4,'multi_stage_research',(
            "ALTER TABLE portal_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'search'",
            "ALTER TABLE portal_jobs ADD COLUMN paper_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE portal_jobs ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE portal_jobs ADD COLUMN stage TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE portal_jobs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'",
        )),
        Migration(5,'leased_worker_execution',(
            "ALTER TABLE portal_jobs ADD COLUMN lease_token TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE portal_jobs ADD COLUMN lease_until INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE portal_jobs ADD COLUMN heartbeat_at INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE portal_jobs ADD COLUMN resume_allowed INTEGER NOT NULL DEFAULT 1",
            'CREATE INDEX IF NOT EXISTS idx_jobs_lease ON portal_jobs(state,lease_until)',
            '''CREATE TABLE IF NOT EXISTS portal_workers (
                id TEXT PRIMARY KEY, last_seen INTEGER NOT NULL,
                state TEXT NOT NULL, active_job INTEGER NOT NULL DEFAULT 0,
                process_id INTEGER NOT NULL DEFAULT 0)''',
            'CREATE INDEX IF NOT EXISTS idx_portal_workers_seen ON portal_workers(last_seen)',
        )),
        Migration(6,'job_stage_events',(
            '''CREATE TABLE IF NOT EXISTS portal_job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('started','completed','failed','cancelled')),
                recorded_at INTEGER NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(job_id) REFERENCES portal_jobs(id) ON DELETE CASCADE)''',
            'CREATE INDEX IF NOT EXISTS idx_job_events_job ON portal_job_events(job_id,id)',
        )),
    ),
    'portal_workspace': (
        Migration(1, 'research_topics_and_papers', (
            '''CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                keywords TEXT NOT NULL, created_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS papers (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                abstract TEXT NOT NULL DEFAULT '', published TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '', doi TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS topic_papers (
                topic_id TEXT NOT NULL, paper_id TEXT NOT NULL,
                PRIMARY KEY(topic_id,paper_id))''',
            '''CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY, paper_id TEXT NOT NULL,
                kind TEXT NOT NULL, dataset TEXT NOT NULL DEFAULT '',
                metric TEXT NOT NULL, value TEXT NOT NULL,
                baseline TEXT NOT NULL DEFAULT '', setting TEXT NOT NULL DEFAULT '',
                page INTEGER NOT NULL, quote TEXT NOT NULL,
                provenance TEXT NOT NULL DEFAULT 'user_import',
                created_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_portal_evidence_paper ON evidence(paper_id)',
        )),
        Migration(2,'pdf_research_evidence',(
            '''CREATE TABLE IF NOT EXISTS pdf_documents (paper_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,
                source TEXT NOT NULL,pages INTEGER NOT NULL DEFAULT 0,analysis_cache TEXT NOT NULL DEFAULT '')''',
            '''CREATE TABLE IF NOT EXISTS pdf_pages (paper_id TEXT NOT NULL,sha256 TEXT NOT NULL,
                page INTEGER NOT NULL,text TEXT NOT NULL,PRIMARY KEY(paper_id,page))''',
            '''CREATE TABLE IF NOT EXISTS verified_evidence (id TEXT PRIMARY KEY,paper_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,kind TEXT NOT NULL,dataset TEXT NOT NULL,
                metric TEXT NOT NULL,value TEXT NOT NULL,baseline TEXT NOT NULL,
                setting TEXT NOT NULL,page INTEGER NOT NULL,quote TEXT NOT NULL,
                provenance TEXT NOT NULL,extractor TEXT NOT NULL)''',
        )),
        Migration(3,'immutable_research_reports',(
            '''CREATE TABLE IF NOT EXISTS research_reports (
                id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL,
                job_id INTEGER,
                generated_at INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                source_digest TEXT NOT NULL,
                paper_count INTEGER NOT NULL,
                finding_count INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                markdown TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_research_reports_topic ON research_reports(topic_id,generated_at DESC,id DESC)',
        )),
        Migration(4,'per_topic_reading_state',(
            '''CREATE TABLE IF NOT EXISTS topic_paper_flags (
                topic_id TEXT NOT NULL, paper_id TEXT NOT NULL,
                is_starred INTEGER NOT NULL DEFAULT 0 CHECK(is_starred IN (0,1)),
                reading_status TEXT NOT NULL DEFAULT 'unread'
                    CHECK(reading_status IN ('unread','reading','done')),
                feedback INTEGER NOT NULL DEFAULT 0 CHECK(feedback IN (-1,0,1)),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(topic_id,paper_id),
                FOREIGN KEY(topic_id,paper_id) REFERENCES topic_papers(topic_id,paper_id)
                    ON DELETE CASCADE)''',
            'CREATE INDEX IF NOT EXISTS idx_portal_flags_star ON topic_paper_flags(topic_id,is_starred)',
        )),
        Migration(5,'tracked_source_metadata_refresh',(
            '''CREATE TABLE IF NOT EXISTS paper_metadata_versions (
                paper_id TEXT PRIMARY KEY, source_updated TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS paper_metadata_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                source_updated TEXT NOT NULL DEFAULT '',
                previous_title TEXT NOT NULL, new_title TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_paper_metadata_history_paper ON paper_metadata_history(paper_id,id)',
        )),
        Migration(6,'pdf_layout_and_batch_checkpoints',(
            '''CREATE TABLE IF NOT EXISTS pdf_layout_rows (
                paper_id TEXT NOT NULL,sha256 TEXT NOT NULL,page INTEGER NOT NULL,
                table_id INTEGER NOT NULL,row_index INTEGER NOT NULL,
                cells_json TEXT NOT NULL,method TEXT NOT NULL,
                PRIMARY KEY(paper_id,sha256,page,table_id,row_index))''',
            'CREATE INDEX IF NOT EXISTS idx_pdf_layout_paper_page ON pdf_layout_rows(paper_id,page)',
            '''CREATE TABLE IF NOT EXISTS pdf_batch_checkpoints (
                paper_id TEXT NOT NULL,sha256 TEXT NOT NULL,cache_key TEXT NOT NULL,
                batch_index INTEGER NOT NULL,material_sha256 TEXT NOT NULL,
                findings_json TEXT NOT NULL,completed_at INTEGER NOT NULL,
                PRIMARY KEY(paper_id,sha256,cache_key,batch_index))''',
            'CREATE INDEX IF NOT EXISTS idx_pdf_batch_paper ON pdf_batch_checkpoints(paper_id)',
            '''CREATE TABLE IF NOT EXISTS pdf_parse_profiles (
                paper_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,diagnostics_json TEXT NOT NULL,
                parsed_at INTEGER NOT NULL)''',
        )),
        Migration(7,'auditable_evidence_and_pair_review', (
            '''CREATE TABLE IF NOT EXISTS evidence_reviews (
                evidence_id TEXT PRIMARY KEY,paper_id TEXT NOT NULL,sha256 TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('approved','rejected','needs_review')),
                conditions_json TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                reviewer_id TEXT NOT NULL, reviewed_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_review_paper ON evidence_reviews(paper_id)',
            '''CREATE TABLE IF NOT EXISTS comparison_pair_reviews (
                id TEXT PRIMARY KEY,topic_id TEXT NOT NULL,
                evidence_a_id TEXT NOT NULL,evidence_b_id TEXT NOT NULL,
                sha_a TEXT NOT NULL,sha_b TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('comparable','incompatible','needs_review')),
                note TEXT NOT NULL,reviewer_id TEXT NOT NULL,reviewed_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_pair_review_topic ON comparison_pair_reviews(topic_id)',
            '''CREATE TABLE IF NOT EXISTS evidence_review_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,evidence_id TEXT NOT NULL,
                paper_id TEXT NOT NULL,sha256 TEXT NOT NULL,decision TEXT NOT NULL,
                conditions_json TEXT NOT NULL,note TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,reviewed_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_review_history_paper ON evidence_review_history(paper_id,id DESC)',
            '''CREATE TABLE IF NOT EXISTS comparison_pair_review_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,topic_id TEXT NOT NULL,
                evidence_a_id TEXT NOT NULL,evidence_b_id TEXT NOT NULL,
                sha_a TEXT NOT NULL,sha_b TEXT NOT NULL,
                decision TEXT NOT NULL,note TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,reviewed_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_pair_history_topic ON comparison_pair_review_history(topic_id,id DESC)',
        )),
        Migration(8,'per_topic_public_discovery_sources', (
            '''CREATE TABLE IF NOT EXISTS topic_sources (
                topic_id TEXT PRIMARY KEY, sources_json TEXT NOT NULL,
                FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE)''',
        )),
        Migration(9,'thousand_paper_workspace_indexes', (
            'CREATE INDEX IF NOT EXISTS idx_portal_papers_recent ON papers(published DESC,id DESC)',
            'CREATE INDEX IF NOT EXISTS idx_portal_papers_oldest ON papers(published ASC,id ASC)',
            'CREATE INDEX IF NOT EXISTS idx_portal_papers_title ON papers(title COLLATE NOCASE,id)',
            'CREATE INDEX IF NOT EXISTS idx_portal_flags_status ON topic_paper_flags(topic_id,reading_status,paper_id)',
            'CREATE INDEX IF NOT EXISTS idx_portal_flags_like ON topic_paper_flags(topic_id,feedback,paper_id)',
            'CREATE INDEX IF NOT EXISTS idx_portal_verified_paper ON verified_evidence(paper_id,sha256,id)',
            'CREATE INDEX IF NOT EXISTS idx_portal_review_evidence_sha ON evidence_reviews(evidence_id,sha256)',
        )),
        Migration(10,'human_reading_notes_and_next_actions', (
            '''CREATE TABLE IF NOT EXISTS paper_notes (
                topic_id TEXT NOT NULL, paper_id TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                next_action TEXT NOT NULL DEFAULT 'none'
                    CHECK(next_action IN ('none','read','replicate','cite','discuss')),
                revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(topic_id,paper_id),
                FOREIGN KEY(topic_id,paper_id) REFERENCES topic_papers(topic_id,paper_id)
                    ON DELETE CASCADE)''',
            'CREATE INDEX IF NOT EXISTS idx_paper_notes_actions ON paper_notes(topic_id,next_action,paper_id)',
        )),
        Migration(11, 'research_claims_and_pdf_anchors', (
            '''CREATE TABLE IF NOT EXISTS research_anchors (
                id TEXT PRIMARY KEY,topic_id TEXT NOT NULL,paper_id TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL,page INTEGER NOT NULL CHECK(page BETWEEN 1 AND 120),
                quote TEXT NOT NULL,quote_start INTEGER NOT NULL,quote_end INTEGER NOT NULL,
                chunk_id INTEGER NOT NULL DEFAULT 0,experiment_id TEXT NOT NULL DEFAULT '',
                note_revision INTEGER NOT NULL DEFAULT 0,
                note_snapshot TEXT NOT NULL DEFAULT '',
                note_action_snapshot TEXT NOT NULL DEFAULT 'none',created_at TEXT NOT NULL,
                FOREIGN KEY(topic_id,paper_id) REFERENCES topic_papers(topic_id,paper_id) ON DELETE CASCADE)''',
            'CREATE INDEX IF NOT EXISTS idx_anchors_topic_paper ON research_anchors(topic_id,paper_id,created_at)',
            '''CREATE TABLE IF NOT EXISTS research_claims (
                id TEXT PRIMARY KEY,topic_id TEXT NOT NULL,body TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('finding','comparison','limitation','next_step')),
                decision TEXT NOT NULL DEFAULT 'draft' CHECK(decision IN ('draft','confirmed')),
                revision INTEGER NOT NULL CHECK(revision>=1),review_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_claims_topic ON research_claims(topic_id,updated_at DESC)',
            '''CREATE TABLE IF NOT EXISTS research_claim_links (
                claim_id TEXT NOT NULL,anchor_id TEXT NOT NULL,topic_id TEXT NOT NULL,
                PRIMARY KEY(claim_id,anchor_id),
                FOREIGN KEY(claim_id) REFERENCES research_claims(id) ON DELETE CASCADE,
                FOREIGN KEY(anchor_id) REFERENCES research_anchors(id) ON DELETE CASCADE)''',
            'CREATE INDEX IF NOT EXISTS idx_claim_links_anchor ON research_claim_links(anchor_id,claim_id)',
            '''CREATE TABLE IF NOT EXISTS research_claim_audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,topic_id TEXT NOT NULL,claim_id TEXT NOT NULL,
                revision INTEGER NOT NULL,decision TEXT NOT NULL,body TEXT NOT NULL,
                anchors_json TEXT NOT NULL,review_note TEXT NOT NULL,event_at TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS idx_claim_audit_topic ON research_claim_audit(topic_id,claim_id,seq)',
            '''CREATE TRIGGER IF NOT EXISTS research_claim_audit_no_update
                BEFORE UPDATE ON research_claim_audit BEGIN
                SELECT RAISE(ABORT, 'research audit is immutable'); END''',
            '''CREATE TRIGGER IF NOT EXISTS research_claim_audit_no_delete
                BEFORE DELETE ON research_claim_audit BEGIN
                SELECT RAISE(ABORT, 'research audit is immutable'); END''',
        )),
    ),
}

# Public accounts each have an isolated workspace SQLite file. The same
# append-only tracking schema is installed per workspace, never in auth.sqlite.
REGISTRIES['portal_workspace'] += (
    Migration(12, 'public_topic_following_and_outbox', REGISTRIES['research'][-2].statements),
    Migration(13, 'public_research_inquiries_v140', REGISTRIES['research'][-1].statements),
)


def migrate(conn: sqlite3.Connection, kind: str) -> int:
    """Apply new steps and return current schema version.

    Existing research databases have already been opened/normalized by core;
    this call records new schema steps, leaving legacy tables intact.
    """
    if kind not in REGISTRIES:
        raise MigrationError('Unknown migration registry')
    steps = REGISTRIES[kind]
    if tuple(s.version for s in steps) != tuple(range(1, len(steps)+1)):
        raise MigrationError('Invalid migration sequence')
    # SQLite DDL participates in explicit transactions. Do not use executescript,
    # which would implicitly commit a caller's transaction.
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            kind TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
            checksum TEXT NOT NULL, applied_at TEXT NOT NULL,
            PRIMARY KEY(kind,version))''')
        rows = conn.execute('SELECT version,name,checksum FROM schema_migrations WHERE kind=? ORDER BY version',(kind,)).fetchall()
        found = {int(row[0]): (row[1],row[2]) for row in rows}
        if any(version>len(steps) for version in found):
            raise MigrationError('Database schema newer than installed application; downgrade refused')
        if len(rows) != (max(found) if found else 0):
            raise MigrationError('Missing migration history entry')
        for step in steps:
            known = found.get(step.version)
            if known:
                if known != (step.name,step.checksum):
                    raise MigrationError('Migration history mismatch: '+step.name)
                continue
            for statement in step.statements:
                conn.execute(statement)
            conn.execute('INSERT INTO schema_migrations VALUES (?,?,?,?,?)',
                (kind,step.version,step.name,step.checksum,datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return len(steps)
    except Exception:
        conn.rollback()
        raise


def assert_compatible(conn: sqlite3.Connection, kind: str) -> None:
    """Reject a newer schema before any legacy initialization can mutate it."""
    target=len(REGISTRIES[kind])
    try:
        row=conn.execute('SELECT MAX(version) FROM schema_migrations WHERE kind=?',(kind,)).fetchone()
    except sqlite3.OperationalError:
        return
    if row and int(row[0] or 0)>target:
        raise MigrationError('Database schema newer than installed application; downgrade refused')


def status(conn: sqlite3.Connection, kind: str) -> dict:
    target=len(REGISTRIES[kind])
    try:
        row=conn.execute('SELECT MAX(version) FROM schema_migrations WHERE kind=?',(kind,)).fetchone()
        current=int(row[0] or 0)
    except sqlite3.OperationalError:
        current=0
    return {'kind':kind,'version':current,'target':target,'current':current==target}
