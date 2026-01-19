-- Agentic Trading OS - Database Initialization
-- PostgreSQL schema for production deployment

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ==========================================
-- Orders Table
-- ==========================================
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(64) UNIQUE NOT NULL,
    client_order_id VARCHAR(64),
    exchange VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    order_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    filled_quantity DECIMAL(20, 8) DEFAULT 0,
    price DECIMAL(20, 8),
    average_price DECIMAL(20, 8),
    fees DECIMAL(20, 8) DEFAULT 0,
    fee_currency VARCHAR(16),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_orders_symbol ON orders(symbol);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_created ON orders(created_at);
CREATE INDEX idx_orders_exchange ON orders(exchange);

-- ==========================================
-- Trades Table
-- ==========================================
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(64) UNIQUE NOT NULL,
    order_id VARCHAR(64) NOT NULL REFERENCES orders(order_id),
    exchange VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    fees DECIMAL(20, 8) DEFAULT 0,
    fee_currency VARCHAR(16),
    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_executed ON trades(executed_at);
CREATE INDEX idx_trades_order ON trades(order_id);

-- ==========================================
-- Positions Table
-- ==========================================
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    position_id VARCHAR(64) UNIQUE NOT NULL,
    exchange VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    current_price DECIMAL(20, 8) NOT NULL,
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,
    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    leverage DECIMAL(10, 2) DEFAULT 1,
    margin DECIMAL(20, 8) DEFAULT 0,
    liquidation_price DECIMAL(20, 8),
    opened_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_open ON positions(closed_at) WHERE closed_at IS NULL;

-- ==========================================
-- Strategy States Table
-- ==========================================
CREATE TABLE IF NOT EXISTS strategy_states (
    id SERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) UNIQUE NOT NULL,
    strategy_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    parameters JSONB DEFAULT '{}',
    state JSONB DEFAULT '{}',
    performance_metrics JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_strategies_name ON strategy_states(strategy_name);
CREATE INDEX idx_strategies_status ON strategy_states(status);

-- ==========================================
-- OHLCV Time Series Table (TimescaleDB optional)
-- ==========================================
CREATE TABLE IF NOT EXISTS ohlcv (
    id SERIAL,
    symbol VARCHAR(32) NOT NULL,
    exchange VARCHAR(32) NOT NULL,
    interval VARCHAR(8) NOT NULL,
    timestamp BIGINT NOT NULL,
    open DECIMAL(20, 8) NOT NULL,
    high DECIMAL(20, 8) NOT NULL,
    low DECIMAL(20, 8) NOT NULL,
    close DECIMAL(20, 8) NOT NULL,
    volume DECIMAL(20, 8) NOT NULL,
    PRIMARY KEY (symbol, exchange, interval, timestamp)
);

CREATE INDEX idx_ohlcv_symbol_interval ON ohlcv(symbol, interval);
CREATE INDEX idx_ohlcv_timestamp ON ohlcv(timestamp);

-- ==========================================
-- Audit Log Table
-- ==========================================
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    entry_id UUID DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    action VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL,
    user_id VARCHAR(64),
    resource_type VARCHAR(64),
    resource_id VARCHAR(128),
    details JSONB DEFAULT '{}',
    ip_address INET,
    chain_hash VARCHAR(64)
);

CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_action ON audit_log(action);
CREATE INDEX idx_audit_user ON audit_log(user_id);

-- ==========================================
-- System Metrics Table
-- ==========================================
CREATE TABLE IF NOT EXISTS system_metrics (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metric_name VARCHAR(128) NOT NULL,
    metric_value DECIMAL(20, 8) NOT NULL,
    tags JSONB DEFAULT '{}'
);

CREATE INDEX idx_metrics_timestamp ON system_metrics(timestamp);
CREATE INDEX idx_metrics_name ON system_metrics(metric_name);

-- ==========================================
-- Functions
-- ==========================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to tables
CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_positions_updated_at BEFORE UPDATE ON positions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_strategies_updated_at BEFORE UPDATE ON strategy_states
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ==========================================
-- Views
-- ==========================================

-- Active positions view
CREATE OR REPLACE VIEW active_positions AS
SELECT * FROM positions WHERE closed_at IS NULL;

-- Recent trades view
CREATE OR REPLACE VIEW recent_trades AS
SELECT * FROM trades ORDER BY executed_at DESC LIMIT 100;

-- Daily PnL view
CREATE OR REPLACE VIEW daily_pnl AS
SELECT
    DATE(executed_at) as trade_date,
    symbol,
    COUNT(*) as trade_count,
    SUM(realized_pnl) as total_pnl,
    SUM(fees) as total_fees,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades
FROM trades
GROUP BY DATE(executed_at), symbol
ORDER BY trade_date DESC;

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO trading;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO trading;
