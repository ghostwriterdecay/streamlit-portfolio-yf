import streamlit as st
import pandas as pd
import datetime as dt
from pathlib import Path

try:
    import yfinance as yf
except Exception:
    yf = None

st.set_page_config(page_title="Private Portfolio Tracker", page_icon="📈", layout="wide")

# ------------------------ AUTH ------------------------
PASSCODE = None
try:
    PASSCODE = st.secrets.get("passcode", None)
except Exception:
    PASSCODE = None

with st.sidebar:
    st.header("🔒 Sign In")
    code = st.text_input("Passcode", type="password")
    st.caption('Set this in `.streamlit/secrets.toml` as: passcode = "your-secret"')

if PASSCODE is None:
    st.error('No passcode found. Add `passcode = "your-secret"` to `.streamlit/secrets.toml`.')
    st.stop()

if code != PASSCODE:
    st.info("Enter the passcode to continue.")
    st.stop()

# ------------------------ DATA PATHS ------------------------
DATA_DIR = Path("data")
DATA_BAL = DATA_DIR / "balances.csv"
DATA_HLD = DATA_DIR / "holdings.csv"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------ HELPERS ------------------------
def load_csv(path: Path, required_cols: dict) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = pd.DataFrame(columns=list(required_cols.keys()))

    for c, default in required_cols.items():
        if c not in df.columns:
            df[c] = default

    return df

def save_csv(path: Path, df: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

def ensure_balances_schema(df: pd.DataFrame) -> pd.DataFrame:
    required = {"month": "", "balance": 0.0, "contribution": 0.0, "note": ""}
    for c, default in required.items():
        if c not in df.columns:
            df[c] = default

    if len(df):
        df["month"] = pd.to_datetime(df["month"], errors="coerce").dt.date
        df["balance"] = pd.to_numeric(df["balance"], errors="coerce").fillna(0.0)
        df["contribution"] = pd.to_numeric(df["contribution"], errors="coerce").fillna(0.0)
        df["note"] = df["note"].fillna("").astype(str)
        df = df.sort_values("month").reset_index(drop=True)

    return df

def ensure_holdings_schema(df: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker": "", "shares": 0.0, "cost_basis": 0.0, "note": ""}
    for c, default in required.items():
        if c not in df.columns:
            df[c] = default

    if len(df):
        df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
        df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0.0)
        df["cost_basis"] = pd.to_numeric(df["cost_basis"], errors="coerce").fillna(0.0)
        df["note"] = df["note"].fillna("").astype(str)

    return df

@st.cache_data(ttl=300)
def fetch_quote(ticker: str):
    if yf is None or not ticker:
        return None
    try:
        t = yf.Ticker(ticker)

        try:
            fi = getattr(t, "fast_info", None)
            if fi is not None:
                if hasattr(fi, "last_price") and fi.last_price is not None:
                    return float(fi.last_price)
                if isinstance(fi, dict):
                    if fi.get("lastPrice") is not None:
                        return float(fi.get("lastPrice"))
                    if fi.get("last_price") is not None:
                        return float(fi.get("last_price"))
        except Exception:
            pass

        hist = t.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])

        info = getattr(t, "info", {}) or {}
        for k in ("regularMarketPrice", "currentPrice", "previousClose"):
            if info.get(k) is not None:
                return float(info[k])

    except Exception:
        return None

    return None

@st.cache_data(ttl=1800)
def fetch_dividends(ticker: str) -> pd.DataFrame:
    if yf is None or not ticker:
        return pd.DataFrame(columns=["Date", "Dividend"])
    try:
        t = yf.Ticker(ticker)
        div = t.dividends
        if div is None or len(div) == 0:
            return pd.DataFrame(columns=["Date", "Dividend"])
        out = div.reset_index()
        out.columns = ["Date", "Dividend"]
        return out
    except Exception:
        return pd.DataFrame(columns=["Date", "Dividend"])

def compute_holdings_market_value(holdings_df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    if len(holdings_df) == 0:
        empty = holdings_df.copy()
        empty["last_price"] = []
        empty["market_value"] = []
        empty["unrealized_pnl"] = []
        return empty, 0.0

    hd = holdings_df.copy()
    prices = []
    for tkr in hd["ticker"].tolist():
        px = fetch_quote(tkr)
        prices.append(px)

    hd["last_price"] = prices
    hd["market_value"] = (hd["last_price"].fillna(0.0) * hd["shares"]).round(2)
    hd["unrealized_pnl"] = ((hd["last_price"].fillna(0.0) - hd["cost_basis"]) * hd["shares"]).round(2)

    total_val = float(hd["market_value"].sum())
    return hd, total_val

def upsert_current_month_balance(balances_df: pd.DataFrame, live_total: float):
    balances_df = ensure_balances_schema(balances_df)
    current_month = dt.date.today().replace(day=1)

    if len(balances_df) and (balances_df["month"] == current_month).any():
        idx = balances_df.index[balances_df["month"] == current_month][0]
        balances_df.at[idx, "balance"] = float(live_total)
        existing_note = str(balances_df.at[idx, "note"]) if "note" in balances_df.columns else ""
        if "Auto-synced from holdings" not in existing_note:
            balances_df.at[idx, "note"] = (existing_note + " | Auto-synced from holdings").strip(" |")
    else:
        balances_df = pd.concat(
            [
                balances_df,
                pd.DataFrame(
                    [{
                        "month": current_month,
                        "balance": float(live_total),
                        "contribution": 0.0,
                        "note": "Auto-synced from holdings"
                    }]
                )
            ],
            ignore_index=True
        )

    balances_df = balances_df.sort_values("month").reset_index(drop=True)
    save_csv(DATA_BAL, balances_df)
    return balances_df

# ------------------------ LOAD DATA ------------------------
bal_required = {"month": "", "balance": 0.0, "contribution": 0.0, "note": ""}
hld_required = {"ticker": "", "shares": 0.0, "cost_basis": 0.0, "note": ""}

balances = ensure_balances_schema(load_csv(DATA_BAL, bal_required))
holdings = ensure_holdings_schema(load_csv(DATA_HLD, hld_required))

# ------------------------ TITLE ------------------------
st.title("📈 Private Portfolio Tracker")
st.caption("Holdings retained, dashboard retained, and current month auto-syncs from holdings.")

# ------------------------ TABS ------------------------
tab_bal, tab_hld, tab_dash = st.tabs(["📅 Monthly Balance", "🧺 Holdings", "📊 Dashboard"])

# ======================== HOLDINGS TAB ========================
with tab_hld:
    st.subheader("Holdings")
    st.caption("Add tickers, shares, and cost basis. Updating holdings also updates the current month balance.")

    with st.form("add_holding"):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
        tkr = c1.text_input("Ticker (e.g., AAPL, SPY)").upper().strip()
        sh = c2.number_input("Shares", min_value=0.0, value=0.0, step=0.01, format="%.2f")
        cb = c3.number_input("Cost Basis ($/share)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
        nt = c4.text_input("Note")
        submitted = st.form_submit_button("Add / Update")

    if submitted and tkr:
        sh = float(sh) if sh not in [None, ""] else 0.0
        cb = float(cb) if cb not in [None, ""] else 0.0
        nt = nt if nt is not None else ""

        if len(holdings) and (holdings["ticker"] == tkr).any():
            idx = holdings.index[holdings["ticker"] == tkr][0]
            holdings.at[idx, "shares"] = sh
            holdings.at[idx, "cost_basis"] = cb
            holdings.at[idx, "note"] = nt
        else:
            holdings = pd.concat(
                [
                    holdings,
                    pd.DataFrame(
                        [{
                            "ticker": tkr,
                            "shares": sh,
                            "cost_basis": cb,
                            "note": nt
                        }]
                    )
                ],
                ignore_index=True
            )

        holdings = ensure_holdings_schema(holdings)
        save_csv(DATA_HLD, holdings)
        st.cache_data.clear()

        hd_after, total_after = compute_holdings_market_value(holdings)
        balances = upsert_current_month_balance(balances, total_after)

        st.success(f"Saved {tkr} and synced current month balance.")

    hd, total_val = compute_holdings_market_value(holdings)

    if len(hd):
        st.dataframe(hd, width="stretch")
        st.metric("Total Market Value", f"${total_val:,.2f}")

        st.divider()
        st.subheader("Dividend Lookup")
        t_opts = hd["ticker"].tolist()
        if t_opts:
            t_sel = st.selectbox("Select ticker for dividend history", t_opts)
            if t_sel:
                divs = fetch_dividends(t_sel)
                if divs.empty:
                    st.info("No dividend data returned.")
                else:
                    st.dataframe(divs.tail(20), width="stretch")

        st.divider()
        st.subheader("Delete a Holding")
        del_t = st.selectbox("Choose ticker to delete", [""] + holdings["ticker"].tolist())
        if st.button("Delete", disabled=(del_t == "")) and del_t:
            holdings = holdings[holdings["ticker"] != del_t].reset_index(drop=True)
            holdings = ensure_holdings_schema(holdings)
            save_csv(DATA_HLD, holdings)
            st.cache_data.clear()

            hd_after, total_after = compute_holdings_market_value(holdings)
            balances = upsert_current_month_balance(balances, total_after)

            st.warning(f"Deleted {del_t} and synced current month balance.")
    else:
        st.info("No holdings saved yet.")

# ======================== MONTHLY BALANCE TAB ========================
with tab_bal:
    st.subheader("Monthly Balance")
    st.caption("Current month updates from holdings automatically. Past months remain frozen unless you edit them manually.")

    current_month = dt.date.today().replace(day=1)
    has_current = len(balances) and (balances["month"] == current_month).any()

    if has_current:
        current_row = balances[balances["month"] == current_month].iloc[0]
        st.metric("Current Month", str(current_month))
        st.metric("Current Month Balance", f"${float(current_row['balance']):,.2f}")
        st.caption("This value auto-syncs from holdings.")
    else:
        st.info("No current month row exists yet. Add or update a holding to create it.")

    st.divider()
    st.subheader("Edit a Saved Month")

    if len(balances):
        month_options = sorted(balances["month"].dropna().tolist())
        selected_month = st.selectbox("Choose month to edit", month_options)

        row = balances[balances["month"] == selected_month].iloc[0]
        is_current_month = selected_month == current_month

        c1, c2 = st.columns(2)
        with c1:
            edit_contrib = st.number_input(
                "Contribution ($)",
                min_value=0.0,
                value=float(row["contribution"]),
                step=25.0,
                key=f"contrib_{selected_month}"
            )
        with c2:
            edit_note = st.text_input(
                "Note",
                value=str(row["note"]),
                key=f"note_{selected_month}"
            )

        if is_current_month:
            st.text_input(
                "Balance ($)",
                value=f"{float(row['balance']):.2f}",
                disabled=True,
                key=f"balance_locked_{selected_month}"
            )
            st.caption("Current month balance is controlled by holdings.")
        else:
            edit_balance = st.number_input(
                "Balance ($)",
                min_value=0.0,
                value=float(row["balance"]),
                step=50.0,
                key=f"balance_{selected_month}"
            )

        if st.button("Save Selected Month"):
            idx = balances.index[balances["month"] == selected_month][0]
            balances.at[idx, "contribution"] = float(edit_contrib)
            balances.at[idx, "note"] = edit_note or ""

            if not is_current_month:
                balances.at[idx, "balance"] = float(edit_balance)

            balances = balances.sort_values("month").reset_index(drop=True)
            save_csv(DATA_BAL, balances)
            st.success(f"Saved {selected_month}.")
    else:
        st.info("No balance history saved yet.")

    st.divider()
    st.subheader("History")
    st.dataframe(balances, width="stretch")

    if len(balances):
        chart_df = balances.copy()
        chart_df["month"] = pd.to_datetime(chart_df["month"])
        chart_df = chart_df.set_index("month")[["balance"]]
        st.line_chart(chart_df)

# ======================== DASHBOARD TAB ========================
with tab_dash:
    st.subheader("Overview")

    hd, total_value = compute_holdings_market_value(holdings)

    net_contrib = float(balances["contribution"].sum()) if len(balances) else 0.0
    current_balance = total_value if len(holdings) else (float(balances["balance"].iloc[-1]) if len(balances) else 0.0)

    st.metric("Current Balance", f"${current_balance:,.2f}")
    st.metric("Holdings Market Value", f"${total_value:,.2f}")
    st.metric("Net Contributions", f"${net_contrib:,.2f}")

    st.divider()
    st.subheader("Balance Over Time")
    if len(balances):
        chart_df = balances.copy()
        chart_df["month"] = pd.to_datetime(chart_df["month"])
        chart_df = chart_df.set_index("month")[["balance"]]
        st.line_chart(chart_df)
    else:
        st.info("Add holdings or a balance record to see the chart.")

st.caption("Data stored in local CSV under /data. For private use only.")
