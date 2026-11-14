
import json
from pathlib import Path

import pandas as pd
import yfinance as yf


# Project paths
BASE_DIR = Path(__file__).resolve().parent.parent
COMPANIES_FILE = BASE_DIR / "companies.json"
OUTPUT_FILE = BASE_DIR / "data" / "prices.json"


def load_companies():
    """Load portfolio and research companies."""

    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        companies = json.load(f)

    return companies["portfolio"] + companies["research"]


def fetch_stock_data(symbol):
    """Download three months of daily stock data."""

    ticker = f"{symbol}.NS"

    print(f"Fetching {ticker}...")

    stock = yf.Ticker(ticker)

    df = stock.history(
        period="3mo",
        interval="1d",
        auto_adjust=False
    )

    if df.empty:
        print(f"WARNING: No data found for {ticker}")
        return None

    # Calculate daily percentage change
    df["daily_change_pct"] = df["Close"].pct_change() * 100

    # Average volume of the PREVIOUS 20 trading sessions
    df["avg_volume_20d"] = (
        df["Volume"].shift(1).rolling(window=20).mean()
    )

    # Today's volume divided by previous 20-day average
    df["volume_ratio"] = (
        df["Volume"] / df["avg_volume_20d"]
    )

    # Convert DataFrame into JSON-friendly records
    records = []

    for date, row in df.iterrows():

        records.append({
            "date": date.strftime("%Y-%m-%d"),
            "close": round(float(row["Close"]), 2),
            "volume": int(row["Volume"]),
            "daily_change_pct": (
                round(float(row["daily_change_pct"]), 2)
                if pd.notna(row["daily_change_pct"]) else None
            ),
            "avg_volume_20d": (
                round(float(row["avg_volume_20d"]), 2)
                if pd.notna(row["avg_volume_20d"]) else None
            ),
            "volume_ratio": (
                round(float(row["volume_ratio"]), 2)
                if pd.notna(row["volume_ratio"]) else None
            )
        })

    return records


def main():

    companies = load_companies()

    all_prices = {}
    successful = 0

    for company in companies:

        symbol = company["nse_symbol"]

        try:
            records = fetch_stock_data(symbol)

            if records:
                all_prices[symbol] = {
                    "name": company["name"],
                    "sector": company["sector"],
                    "records": records
                }

                successful += 1

        except Exception as e:
            print(f"ERROR fetching {symbol}: {e}")

    # Create data directory if it doesn't exist
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Save all results
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_prices, f, indent=4)

    print("\n------------------------------")
    print("PRICE PIPELINE COMPLETED")
    print("------------------------------")

    print(f"Companies processed: {successful}/{len(companies)}")
    print(f"Saved to: {OUTPUT_FILE}")

    # Print latest metrics for every successful stock
    print("\nLATEST STOCK METRICS\n")

    for symbol, stock in all_prices.items():

        latest = stock["records"][-1]

        change = latest["daily_change_pct"]
        ratio = latest["volume_ratio"]

        change_text = (
            f"{change:+.2f}%"
            if change is not None else "N/A"
        )

        ratio_text = (
            f"{ratio:.2f}x average"
            if ratio is not None else "N/A"
        )

        print(f"{symbol}: {change_text}, volume {ratio_text}")


if __name__ == "__main__":
    main()
