import datetime
from src.PolymarketDownloader import PolymarketDownloader

with PolymarketDownloader() as dl:
    markets_df, history_rows = dl.downloadData(
        output_file="data/markets_2023_closed.parquet",

        end_date_min=datetime.datetime(2023, 1, 1),
        end_date_max=datetime.datetime(2023, 12, 31, 23, 59, 59),

        closed=True,
        min_volume=5000,

        fetch_price_history=True
    )