import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def _resolve(data_dir, session_name, default_name) -> Path:
    """Return the correct file path for hardcoded or session data."""
    if data_dir:
        return Path(data_dir) / session_name
    return DATA_DIR / default_name


def _force_columns(df: pd.DataFrame, names: list) -> pd.DataFrame:
    """
    Rename df columns to `names`.  If the column counts differ, truncate or
    pad with generic names so we never raise a length-mismatch error.
    """
    n = len(df.columns)
    if n == len(names):
        df.columns = names
    elif n < len(names):
        df.columns = names[:n]
    else:
        # More columns than expected — keep expected names + label extras generically
        extra = [f"Col{i}" for i in range(len(names), n)]
        df.columns = names + extra
    return df


def load_revenue(data_dir=None) -> pd.DataFrame:
    path = _resolve(data_dir, "revenue.xlsx", "GrainCo_Revenue_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    ncols = len(df.columns)
    if ncols >= 3:
        # First col = Month, last col = Total, middle cols = channels
        channel_names = [f"Channel{i+1}" for i in range(ncols - 2)]
        _force_columns(df, ["Month"] + channel_names + ["Total"])
    else:
        df.columns = [f"Col{i}" for i in range(ncols)]
        df.rename(columns={df.columns[0]: "Month"}, inplace=True)
        if "Total" not in df.columns:
            df["Total"] = 0
    df = df[df["Month"].astype(str).str.upper() != "TOTAL"].dropna(subset=["Month"])
    return df.reset_index(drop=True)


def load_costs(data_dir=None) -> pd.DataFrame:
    path = _resolve(data_dir, "costs.xlsx", "GrainCo_Costs_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    ncols = len(df.columns)
    if ncols >= 3:
        cat_names = [f"Category{i+1}" for i in range(ncols - 2)]
        _force_columns(df, ["Month"] + cat_names + ["Total"])
    else:
        df.columns = [f"Col{i}" for i in range(ncols)]
        df.rename(columns={df.columns[0]: "Month"}, inplace=True)
        if "Total" not in df.columns:
            df["Total"] = 0
    df = df[df["Month"].astype(str).str.upper() != "TOTAL"].dropna(subset=["Month"])
    return df.reset_index(drop=True)


def load_pl(data_dir=None) -> dict:
    path = _resolve(data_dir, "pl.xlsx", "GrainCo_PL_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    _force_columns(df, ["Item", "Amount", "Pct"])
    df = df.dropna(subset=["Item"])
    return {row["Item"]: row["Amount"] for _, row in df.iterrows() if pd.notna(row["Amount"])}


def load_balance_sheet(data_dir=None) -> dict:
    path = _resolve(data_dir, "balance_sheet.xlsx", "GrainCo_BalanceSheet_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    _force_columns(df, ["Item", "Amount"])
    df = df.dropna(subset=["Item"])
    return {row["Item"]: row["Amount"] for _, row in df.iterrows() if pd.notna(row["Amount"])}


def load_trial_balance(data_dir=None) -> pd.DataFrame:
    path = _resolve(data_dir, "trial_balance.xlsx", "GrainCo_TrialBalance_FY2025.xlsx")
    df = pd.read_excel(path, header=1)
    _force_columns(df, ["Account", "Debit", "Credit"])
    df = df.dropna(subset=["Account"])
    return df[df["Account"].astype(str).str.upper() != "TOTAL"].reset_index(drop=True)


# ── JSON loaders (used when AI extraction has run) ────────────────────────────

import json as _json


def _safe_num(v, default: float = 0.0) -> float:
    """Return v if it is a real number, otherwise default."""
    return float(v) if isinstance(v, (int, float)) else default


def load_revenue_json(data_dir) -> pd.DataFrame:
    """Load AI-extracted revenue JSON → DataFrame matching load_revenue() shape."""
    data = _json.loads((Path(data_dir) / "revenue_extracted.json").read_text())
    months = data["months"]
    rows: dict = {"Month": months}
    for channel, values in data["channels"].items():
        rows[channel] = [_safe_num(v) for v in values]
    rows["Total"] = [_safe_num(v) for v in data.get("totals", [0] * len(months))]
    return pd.DataFrame(rows)


def load_costs_json(data_dir) -> pd.DataFrame:
    """Load AI-extracted costs JSON → DataFrame matching load_costs() shape."""
    data = _json.loads((Path(data_dir) / "costs_extracted.json").read_text())
    months = data["months"]
    rows: dict = {"Month": months}
    for cat, values in data["categories"].items():
        rows[cat] = [_safe_num(v) for v in values]
    rows["Total"] = [_safe_num(v) for v in data.get("totals", [0] * len(months))]
    return pd.DataFrame(rows)


def load_pl_json(data_dir) -> dict:
    """Load AI-extracted P&L JSON → dict matching load_pl() shape."""
    data = _json.loads((Path(data_dir) / "pl_extracted.json").read_text())
    return {k: _safe_num(v) for k, v in data.items() if v is not None}


def load_balance_sheet_json(data_dir) -> dict:
    """Load AI-extracted balance sheet JSON → dict matching load_balance_sheet() shape."""
    data = _json.loads((Path(data_dir) / "balance_sheet_extracted.json").read_text())
    return {k: _safe_num(v) for k, v in data.items() if v is not None}


def load_trial_balance_json(data_dir) -> pd.DataFrame:
    """Load AI-extracted trial balance JSON → DataFrame matching load_trial_balance() shape."""
    data = _json.loads((Path(data_dir) / "trial_balance_extracted.json").read_text())
    accounts = data.get("accounts", [])
    return pd.DataFrame([
        {
            "Account": a.get("name", ""),
            "Debit":   _safe_num(a.get("debit",  0)),
            "Credit":  _safe_num(a.get("credit", 0)),
        }
        for a in accounts
    ])
