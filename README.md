# Polymarket Utilities

A Python toolkit for downloading Polymarket market data, testing bias-driven trading strategies, running a simulation bot, and reviewing session results in a desktop analyzer.

The repository also includes the source and rendered outputs for a multi-version Polymarket bias analysis in [`PolymarketBiasAnalysis/`](PolymarketBiasAnalysis/).

> [!IMPORTANT]
> Live order execution is not implemented. `LiveEngine` is currently a stub that raises `NotImplementedError`. Use simulation mode and historical backtests only.

## Features

- Download closed or active Polymarket markets from the Gamma API.
- Stream CLOB price history to Parquet without retaining the full dataset in memory.
- Run configurable new-market, category-bias, breakout, and late-stage strategies.
- Replay strategies against historical market and price-history Parquet files.
- Record trades, portfolio snapshots, summaries, and reports by session.
- Explore sessions and launch backtests from a Dear PyGui desktop application.
- Review the R Markdown, HTML, PDF, and chart outputs from the bias analysis.

## Project structure

```text
PolymarketBiasAnalysis/   R Markdown analyses and rendered reports
src/analyzer/             Session analyzer GUI and report generation
src/bot/                  Trading engine, strategies, execution, and models
src/bot/backtest/         Historical replay engine and Parquet loaders
src/PolymarketDownloader.py
data/                     Local Parquet datasets (ignored by Git)
logs/                     Generated session logs and reports
run_bot.py                Simulation bot entry point
run_backtest.py           Backtest CLI
run_analyzer.py           Analyzer GUI entry point
```

## Setup

Python 3.12 is recommended. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On macOS or Linux, activate the environment with:

```bash
source .venv/bin/activate
```

## Configuration

The bot reads `config.json` by default. It controls:

- simulation bankroll and scan interval;
- volume, spread, exposure, and loss limits;
- enabled strategies and their thresholds or weights;
- time-priority scoring.

Keep `mode` set to `"simulation"`. You can use a different configuration file with `--config`.

Sensitive local files such as `api_keys.json` are ignored by Git. Never commit wallet private keys, API secrets, downloaded Parquet data, or session output containing sensitive information.

## Download historical data

Edit the date range and output path in `src/main.py`, then run:

```powershell
python src/main.py
```

The downloader writes a markets file such as:

```text
data/markets_2023_closed.parquet
```

When price history is enabled, it also creates:

```text
data/markets_2023_closed_price_history.parquet
```

Downloaded `*.parquet` files are intentionally ignored because they can be large.

## Run the simulation bot

```powershell
python run_bot.py
```

Useful options:

```powershell
python run_bot.py --config config.json --mode simulation
python run_bot.py --verbose
```

Stop the bot with `Ctrl+C`. Session artifacts are exported to `logs/`.

## Run a historical backtest

```powershell
python run_backtest.py `
  --markets data/markets_2025_closed.parquet `
  --prices data/markets_2025_closed_price_history.parquet `
  --start 2025-01-01 `
  --end 2025-12-31 `
  --bankroll 10000 `
  --interval 60
```

On macOS or Linux, replace PowerShell's backtick line continuations with `\`.

Backtests use the same strategy configuration as the simulation bot and write their session results to `logs/`.

## Analyze results

Launch the desktop analyzer after running a simulation or backtest:

```powershell
python run_analyzer.py
```

The analyzer discovers `logs/session_*` artifacts and provides portfolio charts, performance statistics, strategy breakdowns, trade tables, backtest controls, and plain-text report generation.

## Bias analysis

`PolymarketBiasAnalysis/` contains successive R Markdown analyses together with rendered HTML/PDF reports and chart assets. The generated R Markdown cache directories are deliberately excluded from the repository because they are reproducible and substantially larger than the reports themselves.

## Disclaimer

This project is experimental research software, not financial advice. Prediction-market trading carries risk; validate assumptions and strategies independently before using real funds.
