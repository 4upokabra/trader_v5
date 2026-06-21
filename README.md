# Trader V5 — Hybrid Crypto Trading Bot

Гибридная торговая система: **Module A** (LightGBM trend signal + Claude risk overlay) + **Module B** (Funding Rate Arbitrage). Биржа: OKX EU (MiCA-лицензирован). Расчёты в USDC.

## Быстрый старт

### 1. Настройка окружения

```bash
cp .env.example .env
# Заполни .env: OKX_API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN и т.д.
```

### 2. Скачать исторические данные (для бэктеста Module A)

```bash
bash scripts/download_data.sh
```

### 3. Бэктест Module A

```bash
bash scripts/run_backtest_a.sh 20220101-20250101
```

### 4. Запуск в режиме dry-run (без реальных денег)

В `module_a/user_data/config.json` установи `"dry_run": true`, затем:

```bash
docker compose up -d
```

Grafana доступна на http://localhost:3000 (логин: admin / пароль из POSTGRES_PASSWORD).

### 5. Canary-запуск (10% капитала)

После 2 месяцев dry-run:
1. Установи `"dry_run": false` в config.json
2. Установи `MODULE_B_CAPITAL_USDC` в .env = 10% от целевого
3. Перезапусти: `docker compose up -d`

## Структура проекта

```
trader_v5/
├── docker-compose.yml
├── .env.example
├── module_a/
│   ├── Dockerfile                          # Freqtrade + LightGBM
│   ├── user_data/
│   │   ├── config.json                     # Freqtrade конфиг (OKX EU, USDC)
│   │   └── strategies/
│   │       └── TrendMLStrategy.py          # FreqAI + Triple Barrier
│   └── overlay/
│       ├── overlay_service.py              # Claude API daily scanner
│       └── Dockerfile
├── module_b/
│   ├── main.py                             # Funding arb main loop
│   ├── scanner.py                          # Funding rate scanner
│   ├── executor.py                         # Open/close positions
│   ├── monitor.py                          # Delta drift + margin
│   ├── risk.py                             # Circuit breaker + limits
│   └── Dockerfile
├── shared/
│   ├── circuit_breaker.py                  # System-wide halt logic
│   ├── equity_tracker.py                   # Equity snapshots → DB
│   ├── alerts/telegram.py                  # Telegram alerts
│   ├── config.py                           # Shared config from env
│   ├── db/init.sql                         # DB schema
│   └── Dockerfile
├── monitoring/
│   └── grafana/                            # Dashboard + datasource
└── scripts/
    ├── download_data.sh                    # Скачать OHLCV для бэктеста
    ├── run_backtest_a.sh                   # Запустить бэктест Module A
    ├── backtest_module_b.py                # Walk-forward бэктест Module B
    └── shap_analysis.py                    # SHAP важность фич
```

## Архитектура

```
Risk & Monitoring Layer (circuit_breaker + equity_tracker)
          │                         │
   Module A: Freqtrade        Module B: Funding Arb
   LightGBM (FreqAI)          CCXT async, delta-neutral
   Claude overlay              Long spot + Short perp
   ~60% капитала               ~40% капитала
          │                         │
              OKX Europe Ltd (MiCA)
```

## Важные параметры (.env)

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `MODULE_B_FUNDING_THRESHOLD` | 0.0005 | Мин. funding rate для входа (0.05%/8h ≈ 21% APY) |
| `MODULE_B_NEGATIVE_FUNDING_N` | 3 | Кол-во подряд отриц. периодов перед закрытием |
| `MODULE_B_DELTA_TOLERANCE` | 0.005 | Допуск дрейфа дельты перед ребалансировкой |
| `CIRCUIT_BREAKER_DRAWDOWN` | 0.15 | Hard stop (15% просадка) |
| `OVERLAY_SENTIMENT_THRESHOLD` | -0.5 | Порог sentiment для снижения позиции вдвое |
| `OVERLAY_OBSERVE_ONLY` | false | true = оверлей только логирует, не влияет на торговлю |

## Регуляторные требования (Нидерланды, ЕС)

- Использовать только OKX Europe Ltd (не глобальный OKX)
- Все пары в USDC, не USDT (USDT делистируется с EU-бирж по MiCA)
- DAC8 с 2027: вести собственный учёт PnL из таблицы `module_b_positions` и Freqtrade trades
- Проверять статус CASP-лицензии на реестре ESMA перед деплоем

## Деплой на Linux VPS

```bash
# Минимум 4 vCPU / 8GB RAM для обучения LightGBM
git clone <repo> trader_v5
cd trader_v5
cp .env.example .env
# Заполни .env
docker compose up -d
```

Для автообновления конфига OKX API ключей в Freqtrade используй:
`docker compose exec module_a freqtrade trade --config /freqtrade/user_data/config.json`
