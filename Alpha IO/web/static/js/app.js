/**
 * Agentic Trading OS - Web Dashboard JavaScript
 */

// =============================================================================
// Global State
// =============================================================================

let priceChart = null;
let eventSource = null;
let lastPrices = {};

// =============================================================================
// Real-time Updates
// =============================================================================

function startRealTimeUpdates() {
    // Use Server-Sent Events for real-time updates
    if (typeof EventSource !== 'undefined') {
        eventSource = new EventSource('/api/stream');

        eventSource.onmessage = function(event) {
            const stats = JSON.parse(event.data);
            updateDashboard(stats);
        };

        eventSource.onerror = function() {
            console.log('SSE connection lost, falling back to polling');
            eventSource.close();
            startPolling();
        };
    } else {
        startPolling();
    }
}

function startPolling() {
    // Fallback to polling if SSE not supported
    setInterval(fetchStats, 2000);
}

function fetchStats() {
    fetch('/api/stats')
        .then(response => response.json())
        .then(stats => updateDashboard(stats))
        .catch(err => console.error('Failed to fetch stats:', err));
}

// =============================================================================
// Dashboard Updates
// =============================================================================

function updateDashboard(stats) {
    // Update header stats
    updateElement('header-portfolio', formatCurrency(stats.current_capital));
    updateElement('header-pnl', formatPnL(stats.total_pnl));
    updateElement('header-uptime', stats.uptime_formatted);

    // Update stat cards
    updateElement('stat-portfolio', formatCurrency(stats.current_capital));
    updateElement('stat-pnl', formatPnL(stats.total_pnl) + ` (${stats.pnl_percent.toFixed(2)}%)`);
    updateElement('stat-trades', stats.total_trades);
    updateElement('stat-winrate', stats.win_rate.toFixed(1) + '%');

    // Update status indicators
    updateSystemStatus(stats.is_running);

    // Update prices
    updatePrices(stats.prices);

    // Update positions count
    updateElement('status-uptime', stats.uptime_formatted);
}

function updateElement(id, value) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = value;
    }
}

function updateSystemStatus(isRunning) {
    const statusEl = document.getElementById('system-status');
    if (statusEl) {
        const dot = statusEl.querySelector('.status-dot');
        const text = statusEl.querySelector('.status-text');

        if (dot) {
            dot.className = 'status-dot ' + (isRunning ? 'running' : 'stopped');
        }
        if (text) {
            text.textContent = isRunning ? 'Running' : 'Stopped';
        }
    }
}

function updatePrices(prices) {
    const priceList = document.getElementById('price-list');
    if (!priceList || !prices || Object.keys(prices).length === 0) return;

    let html = '';
    for (const [symbol, price] of Object.entries(prices)) {
        const change = lastPrices[symbol]
            ? ((price - lastPrices[symbol]) / lastPrices[symbol] * 100).toFixed(2)
            : 0;

        const changeClass = change > 0 ? 'positive' : change < 0 ? 'negative' : 'neutral';
        const changeSign = change > 0 ? '+' : '';

        html += `
            <div class="price-item">
                <span class="symbol">${symbol}</span>
                <span class="price">${formatCurrency(price)}</span>
                <span class="change ${changeClass}">${changeSign}${change}%</span>
            </div>
        `;

        lastPrices[symbol] = price;
    }

    priceList.innerHTML = html;
}

// =============================================================================
// Price Chart
// =============================================================================

function initPriceChart() {
    const canvas = document.getElementById('priceChart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');

    priceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Price',
                data: [],
                borderColor: '#6366f1',
                backgroundColor: 'rgba(99, 102, 241, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.4,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                x: {
                    display: true,
                    grid: {
                        display: false
                    },
                    ticks: {
                        color: '#64748b',
                        maxTicksLimit: 10
                    }
                },
                y: {
                    display: true,
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)'
                    },
                    ticks: {
                        color: '#64748b',
                        callback: function(value) {
                            return '$' + value.toLocaleString();
                        }
                    }
                }
            },
            interaction: {
                intersect: false,
                mode: 'index'
            }
        }
    });

    // Load initial data
    loadPriceHistory('AAPL');

    // Symbol selector
    const symbolSelect = document.getElementById('chart-symbol');
    if (symbolSelect) {
        symbolSelect.addEventListener('change', function() {
            loadPriceHistory(this.value);
        });
    }
}

function loadPriceHistory(symbol) {
    fetch(`/api/price-history/${encodeURIComponent(symbol)}`)
        .then(response => response.json())
        .then(history => {
            if (priceChart && history.length > 0) {
                const labels = history.map(h => {
                    const date = new Date(h.time);
                    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                });
                const prices = history.map(h => h.price);

                priceChart.data.labels = labels;
                priceChart.data.datasets[0].data = prices;
                priceChart.update('none');
            }
        })
        .catch(err => console.error('Failed to load price history:', err));
}

function addPricePoint(symbol, price) {
    if (!priceChart) return;

    const currentSymbol = document.getElementById('chart-symbol')?.value;
    if (currentSymbol !== symbol) return;

    const now = new Date();
    const label = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    priceChart.data.labels.push(label);
    priceChart.data.datasets[0].data.push(price);

    // Keep only last 100 points
    if (priceChart.data.labels.length > 100) {
        priceChart.data.labels.shift();
        priceChart.data.datasets[0].data.shift();
    }

    priceChart.update('none');
}

// =============================================================================
// Utility Functions
// =============================================================================

function formatCurrency(value) {
    return '$' + parseFloat(value).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

function formatPnL(value) {
    const formatted = formatCurrency(Math.abs(value));
    return (value >= 0 ? '+' : '-') + formatted;
}

function formatPercent(value) {
    const sign = value >= 0 ? '+' : '';
    return sign + value.toFixed(2) + '%';
}

function formatTime(isoString) {
    const date = new Date(isoString);
    return date.toLocaleString();
}

// =============================================================================
// Notifications
// =============================================================================

function showNotification(type, message, duration = 5000) {
    const container = document.querySelector('.content-wrapper');
    if (!container) return;

    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.textContent = message;

    container.insertBefore(alert, container.firstChild);

    setTimeout(() => {
        alert.style.opacity = '0';
        setTimeout(() => alert.remove(), 300);
    }, duration);
}

// =============================================================================
// API Helpers
// =============================================================================

async function apiCall(endpoint, method = 'GET', data = null) {
    const options = {
        method,
        headers: {
            'Content-Type': 'application/json'
        }
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    try {
        const response = await fetch(endpoint, options);
        return await response.json();
    } catch (error) {
        console.error('API call failed:', error);
        throw error;
    }
}

// =============================================================================
// Initialization
// =============================================================================

document.addEventListener('DOMContentLoaded', function() {
    // Initialize components based on page
    if (document.getElementById('priceChart')) {
        initPriceChart();
    }

    // Start real-time updates
    if (document.querySelector('.dashboard') || document.querySelector('.admin-page')) {
        startRealTimeUpdates();
    }

    // Handle form submissions with AJAX
    document.querySelectorAll('form[data-ajax]').forEach(form => {
        form.addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData(form);
            const data = Object.fromEntries(formData.entries());

            try {
                const result = await apiCall(form.action, 'POST', data);
                if (result.success) {
                    showNotification('success', result.message || 'Success');
                } else {
                    showNotification('error', result.error || 'An error occurred');
                }
            } catch (error) {
                showNotification('error', 'Request failed');
            }
        });
    });
});

// =============================================================================
// Cleanup
// =============================================================================

window.addEventListener('beforeunload', function() {
    if (eventSource) {
        eventSource.close();
    }
});
