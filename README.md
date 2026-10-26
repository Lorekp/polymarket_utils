# Polymarket Utilities

A Python research toolkit for collecting Polymarket data, replaying configurable strategies over historical prices, and inspecting simulated trading sessions in a desktop application. The repository also contains successive R Markdown studies of market calibration and bias.

**Status:** experimental downloader, simulation, and historical replay software. Live order placement and position closing raise `NotImplementedError` in `LiveEngine`; the `live` CLI option does not provide working real-money execution. Results do not establish a profitable strategy.

## What is implemented

- Gamma event/market collection with filters and Parquet output.
- Asynchronous CLOB price-history requests with concurrency limits, rate limiting, retries, and incremental Parquet writes.
- Configurable new-market, category-bias, breakout, and late-stage strategy components.
- A historical replay pipeline that shares strategies, scoring, portfolio management, and logging with the simulation bot.
- Per-token timestamp indexes for historical price lookup at or before simulated time.
- A Dear PyGui analyzer with session selection, equity charts, statistics, trade tables, backtest controls, and text-report export.
- R Markdown source, rendered HTML/PDF reports, and figures in `PolymarketBiasAnalysis/`.

## Setup

Use Python 3.12 or newer and a desktop environment for the GUI. The pinned requirements include `pywin32`, so the unchanged installation instructions target Windows. Other platforms require adapting the dependency list; cross-platform installation is not established.

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Downloading and running the simulation require network access to Polymarket. Historical replay requires local Parquet datasets, which are not bundled.

## Download data

Edit `src/main.py` to select dates, output filename, and filters. Its supplied example selects closed markets ending during 2023 with a minimum volume of 5,000 and enables price history. Create the output directory and run it as a module so the repository-root imports resolve:

```powershell
New-Item -ItemType Directory -Force data
python -m src.main
```

The example writes `data/markets_2023_closed.parquet` and, when history is returned, `data/markets_2023_closed_price_history.parquet`. Downloads depend on API availability and retained historical coverage; review warnings for failed or empty histories before using the files.

## Historical replay

With matching market and history files available:

```powershell
python run_backtest.py --markets data/markets_2023_closed.parquet --prices data/markets_2023_closed_price_history.parquet --start 2023-01-01 --end 2023-12-31 --bankroll 10000 --interval 60 --config config.json
```

`--interval` is measured in minutes. The runner creates session artifacts under `logs/`. Change the dates and filenames together when using another dataset.

## Simulation and analysis

Keep `mode` set to `"simulation"` in `config.json`. The file also controls scan frequency, bankroll, volume/spread filters, exposure limits, strategy thresholds, and scoring weights. Category biases and sizing fractions are configured assumptions, not automatically validated estimates.

```powershell
python run_bot.py --config config.json
python run_analyzer.py
```

Stop the simulation with `Ctrl+C`. Launch the analyzer to inspect saved sessions or configure a replay through its Backtest tab. The simulation consumes current market data but uses simulated execution.

## Layout

| Path | Purpose |
| --- | --- |
| `src/PolymarketDownloader.py` | Collection, filtering, and Parquet writing |
| `src/bot/` | Market scanning, strategies, scoring, portfolio, and logging |
| `src/bot/execution/` | Simulation execution and the live stub |
| `src/bot/backtest/` | Simulated clock, data adapter, indexes, and replay runner |
| `src/analyzer/` | Desktop views and report generation |
| `PolymarketBiasAnalysis/` | R studies and rendered reports |
| `config.json` | Default simulation and strategy settings |

## Research limits and validation

Historical prices are time-indexed, but market metadata such as volume and liquidity comes from downloaded snapshots. The replay is not a reconstruction of historical order books or guaranteed point-in-time metadata. Its execution engine applies fixed 0.5% entry slippage and simplifies fills and settlement. Reported returns therefore require independent checks for selection bias, future information, costs, and execution feasibility.

The R studies require R, their declared packages, and external market/history files. Their source files read from `../data` relative to `PolymarketBiasAnalysis/`, or from the directory set in `POLYMARKET_DATA_DIR`. Use matching 2025 files for those studies; the Python downloader example above selects 2023 and must be adjusted to collect another period. No R environment lockfile is provided.

The checked-in HTML/PDF reports are historical research outputs, not newly reproduced results. Personal workstation paths in the R Markdown and HTML examples were made portable for publication; numerical results were not recomputed. The original PDFs remain as historical exports and may show the old local paths.

No automated test suite or CI workflow is included in this snapshot, although `pytest` appears in the requirements. There is no established passing test command or bundled smoke dataset.

## Attribution and license

The R reports identify their author as Laurence. The Python implementation integrates third-party packages including HTTPX, pandas, PyArrow, NumPy, `polymarket-apis`, and Dear PyGui; their respective licenses continue to apply. This repository contains no project license file specifying reuse permissions.
