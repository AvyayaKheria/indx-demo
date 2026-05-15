import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def load_revenue() -> pd.DataFrame:
    df = pd.read_excel(DATA_DIR / "GrainCo_Revenue_FY2025.xlsx", header=1)
    df.columns = ["Month", "Dine_In", "Delivery", "Catering", "Total"]
    df = df[df["Month"] != "TOTAL"].dropna(subset=["Month"])
    return df.reset_index(drop=True)


def load_costs() -> pd.DataFrame:
    df = pd.read_excel(DATA_DIR / "GrainCo_Costs_FY2025.xlsx", header=1)
    df.columns = ["Month", "COGS", "Payroll", "Rent", "Marketing", "Total"]
    df = df[df["Month"] != "TOTAL"].dropna(subset=["Month"])
    return df.reset_index(drop=True)


def load_pl() -> dict:
    df = pd.read_excel(DATA_DIR / "GrainCo_PL_FY2025.xlsx", header=1)
    df.columns = ["Item", "Amount", "Pct"]
    df = df.dropna(subset=["Item"])
    return {row["Item"]: row["Amount"] for _, row in df.iterrows() if pd.notna(row["Amount"])}


def load_balance_sheet() -> dict:
    df = pd.read_excel(DATA_DIR / "GrainCo_BalanceSheet_FY2025.xlsx", header=1)
    df.columns = ["Item", "Amount"]
    df = df.dropna(subset=["Item"])
    return {row["Item"]: row["Amount"] for _, row in df.iterrows() if pd.notna(row["Amount"])}


def load_trial_balance() -> pd.DataFrame:
    df = pd.read_excel(DATA_DIR / "GrainCo_TrialBalance_FY2025.xlsx", header=1)
    df.columns = ["Account", "Debit", "Credit"]
    df = df.dropna(subset=["Account"])
    return df[df["Account"] != "TOTAL"].reset_index(drop=True)
