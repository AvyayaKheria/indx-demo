import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def _resolve(data_dir, session_name, default_name) -> Path:
    """Return the correct file path for hardcoded or session data."""
    if data_dir:
        return Path(data_dir) / session_name
    return DATA_DIR / default_name


def load_revenue(data_dir=None) -> pd.DataFrame:
    path = _resolve(data_dir, "revenue.xlsx", "GrainCo_Revenue_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    df.columns = ["Month", "Dine_In", "Delivery", "Catering", "Total"]
    df = df[df["Month"] != "TOTAL"].dropna(subset=["Month"])
    return df.reset_index(drop=True)


def load_costs(data_dir=None) -> pd.DataFrame:
    path = _resolve(data_dir, "costs.xlsx", "GrainCo_Costs_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    df.columns = ["Month", "COGS", "Payroll", "Rent", "Marketing", "Total"]
    df = df[df["Month"] != "TOTAL"].dropna(subset=["Month"])
    return df.reset_index(drop=True)


def load_pl(data_dir=None) -> dict:
    path = _resolve(data_dir, "pl.xlsx", "GrainCo_PL_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    df.columns = ["Item", "Amount", "Pct"]
    df = df.dropna(subset=["Item"])
    return {row["Item"]: row["Amount"] for _, row in df.iterrows() if pd.notna(row["Amount"])}


def load_balance_sheet(data_dir=None) -> dict:
    path = _resolve(data_dir, "balance_sheet.xlsx", "GrainCo_BalanceSheet_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    df.columns = ["Item", "Amount"]
    df = df.dropna(subset=["Item"])
    return {row["Item"]: row["Amount"] for _, row in df.iterrows() if pd.notna(row["Amount"])}


def load_trial_balance(data_dir=None) -> pd.DataFrame:
    path = _resolve(data_dir, "trial_balance.xlsx", "GrainCo_TrialBalance_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    df.columns = ["Account", "Debit", "Credit"]
    df = df.dropna(subset=["Account"])
    return df[df["Account"] != "TOTAL"].reset_index(drop=True)
