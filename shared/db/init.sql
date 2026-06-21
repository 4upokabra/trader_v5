-- ─── Module A: LightGBM signal decisions ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS module_a_decisions (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pair            TEXT        NOT NULL,
    timeframe       TEXT        NOT NULL,
    signal          SMALLINT    NOT NULL,  -- 1 long, -1 short, 0 neutral
    lgbm_prob       REAL,
    risk_pct        REAL,                  -- position size fraction (post-overlay)
    overlay_applied BOOLEAN     NOT NULL DEFAULT FALSE,
    trade_id        BIGINT                 -- FK to freqtrade trades table (nullable)
);
CREATE INDEX ON module_a_decisions (pair, created_at DESC);

-- ─── Claude overlay log ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS overlay_log (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pair            TEXT        NOT NULL,
    sentiment       REAL,                  -- -1.0 to 1.0
    anomaly_flag    BOOLEAN     NOT NULL DEFAULT FALSE,
    anomaly_reason  TEXT,
    confidence      REAL,
    action          TEXT,                  -- 'veto' | 'reduce_50' | 'pass'
    raw_response    JSONB
);
CREATE INDEX ON overlay_log (pair, created_at DESC);

-- ─── Module B: Funding arb positions ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS module_b_positions (
    id              BIGSERIAL PRIMARY KEY,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    pair            TEXT        NOT NULL,
    spot_size       NUMERIC(20,8) NOT NULL,
    perp_size       NUMERIC(20,8) NOT NULL,
    entry_spot_px   NUMERIC(20,8) NOT NULL,
    entry_perp_px   NUMERIC(20,8) NOT NULL,
    exit_spot_px    NUMERIC(20,8),
    exit_perp_px    NUMERIC(20,8),
    funding_collected NUMERIC(20,8) NOT NULL DEFAULT 0,
    status          TEXT        NOT NULL DEFAULT 'open',  -- open | closed | liquidated
    close_reason    TEXT,
    pnl_usdc        NUMERIC(20,8)
);
CREATE INDEX ON module_b_positions (pair, status);

-- ─── Module B: Funding rate history ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS funding_rates (
    id              BIGSERIAL PRIMARY KEY,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pair            TEXT        NOT NULL,
    funding_rate    NUMERIC(20,10) NOT NULL,
    next_funding_ts TIMESTAMPTZ
);
CREATE INDEX ON funding_rates (pair, recorded_at DESC);
-- prevent duplicate collection
CREATE UNIQUE INDEX ON funding_rates (pair, next_funding_ts);

-- ─── System equity snapshots (for circuit breaker & Grafana) ─────────────────
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    module_a_equity NUMERIC(20,8),
    module_b_equity NUMERIC(20,8),
    total_equity    NUMERIC(20,8) NOT NULL,
    drawdown_pct    REAL,
    peak_equity     NUMERIC(20,8)
);
CREATE INDEX ON equity_snapshots (recorded_at DESC);

-- ─── System events log (circuit breaker, alerts, errors) ─────────────────────
CREATE TABLE IF NOT EXISTS system_events (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT        NOT NULL,  -- 'circuit_breaker' | 'module_a' | 'module_b' | 'overlay'
    level           TEXT        NOT NULL,  -- 'info' | 'warning' | 'critical'
    message         TEXT        NOT NULL,
    details         JSONB
);
CREATE INDEX ON system_events (source, level, occurred_at DESC);
