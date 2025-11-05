import streamlit as st
import pandas as pd
import datetime as dt
from pathlib import Path

# yfinance is optional during import in case of cold env problems; we fail gracefully
try:
    import yfinance as yf
except Exception:
    yf = None

st.set_page_config(page_title="Private Portfolio Tracker", page_icon="📈", layout="wide")

# ------------------------ AUTH (single-user passcode) ------------------------
PASSCODE = None
try:
    # Streamlit Cloud / local secrets.toml
    PASSCODE = st.secrets.get("passcode", None)
except Exception:
    PASSCODE = None

with st.sidebar:
    st.header("🔒 Sign In")
    code = st.text_input("Passcode", type="password")
    st.caption("Set this in `.streamlit/secrets.toml` (passcode = \"your-secret\")")

if PASSCODE is None:
    st.error("No passcode found. Add `passcode = \"your-secret\"` to `.streamlit/secrets.toml`.")
    st.stop()

if code != PASSCODE:
    st.info("Enter the passcode to continue.")
    st.stop()

# ------------------------ DATA PATHS & HELPERS ------------------------
DATA_BAL = Path("data/balances.csv")
DATA_HLD = Path("data/holdings.csv")
DATA_BAL.parent.mkdir(parents=True, exist_ok=True)

def load_csv(path: Path, required_cols: dict) -> pd.DataFrame:
    """Load a CSV and ensure required columns exist with default values."""
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = pd.DataFrame(columns=list(required_cols.keys()))
    # add any missing cols with defaults
    for c, default in required_cols.items():
        if c not in df.columns:
            df[c] = default
    return df

def save_csv(path: Path, df: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

def _safe_to_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

# --- yfinance helpers (robust across versions) ---
@st.cache_data(ttl=300)
def fetch_quote(ticker: str):
    if yf is None:
        return None
    try:
        t = yf.Ticker(ticker)
        # 1) try fast_info attr style
        try:
            fi = getattr(t, "fast_info", None)
            # fast_info can be an object with attributes OR dict-like
            if fi is not None:
                # attribute style
                if hasattr(fi, "last_price") and fi.last_price is not None:
                    return float(fi.last_price)
                # dict-like style
                if isinstance(fi, dict) and ("lastPrice" in fi or "last_price" in fi):
                    return float(fi.get("lastPrice", fi.get("last_price")))
        except Exception:
            pass
        # 2) last close
        hist = t.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        # 3) info (slow; fallback only)
        info = getattr(t, "info", {}) or {}
        for k in ("regularMarketPrice", "currentPrice", "previousClose"):
            if k in info and info[k] is not None:
                return float(info[k])
    except Exception:
        return None
    return None

@st.cache_data(ttl=1800)
def fetch_dividends(ticker: str) -> pd.DataFrame:
    if yf is None:
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

# ---------- Load data
bal_required = {"month": "", "balance": 0.0, "contribution": 0.0, "note": ""}
hld_required = {"ticker": "", "shares": 0.0, "cost_basis": 0.0, "note": ""}

balances = load_csv(DATA_BAL, bal_required)
holdings = load_csv(DATA_HLD, hld_required)

# normalize types
if len(balances):
    balances["month"] = pd.to_datetime(balances["month"], errors="coerce").dt.date
    balances["balance"] = pd.to_numeric(balances["balance"], errors="coerce").fillna(0.0)
    balances["contribution"] = pd.to_numeric(balances["contribution"], errors="coerce").fillna(0.0)

if len(holdings):
    holdings["ticker"] = holdings["ticker"].astype(str).str.upper().str.strip()
    holdings["shares"] = pd.to_numeric(holdings["shares"], errors="coerce").fillna(0.0)
    holdings["cost_basis"] = pd.to_numeric(holdings["cost_basis"], errors="coerce").fillna(0.0)

st.title("📈 Private Portfolio Tracker")
st.caption("Single-user passcode, monthly balance updates, equity holdings with live Yahoo Finance prices.")

# ------------------------ TABS ------------------------
tab_bal, tab_hld, tab_dash = st.tabs(["📅 Monthly Balance", "🧺 Holdings", "📊 Dashboard"])

# ===== Monthly Balance Tab =====
with tab_bal:
    st.subheader("➕ Add / Update Month")
    col1, col2 = st.columns(2)
    with col1:
        month = st.date_input("Month", dt.date.today().replace(day=1))
    with col2:
        default_bal = float(balances["balance"].iloc[-1]) if len(balances) else 3000.0
        balance = st.number_input("Ending Balance ($)", min_value=0.0, value=default_bal, step=50.0)
    col3, col4 = st.columns(2)
    with col3:
        contrib = st.number_input("Contribution This Month ($)", value=0.0, step=25.0)
    with col4:
        note = st.text_input("Note", value="")

    sync = st.checkbox("Sync balance from current holdings value", value=False,
                       help="Fetch latest prices for all holdings and use total value as the month balance.")

    if st.button("Save/Update Month", use_container_width=True):
        if sync and len(holdings):
            total = 0.0
            for _, r in holdings.iterrows():
                tkr = (r.get("ticker") or "").strip()
                sh = _safe_to_float(r.get("shares"), 0.0)
                if tkr and sh > 0:
                    px = fetch_quote(tkr)
                    if px is not None:
                        total += float(px) * sh
            balance = total
        m1 = month.replace(day=1)
        if len(balances) and (balances["month"] == m1).any():
            balances.loc[balances["month"] == m1, ["balance", "contribution", "note"]] = [balance, contrib, note]
        else:
            balances = pd.concat(
                [balances, pd.DataFrame([{"month": m1, "balance": balance, "contribution": contrib, "note": note}])],
                ignore_index=True
            )
        balances = balances.sort_values("month")
        save_csv(DATA_BAL, balances)
        st.success(f"Saved {m1.isoformat()} → ${balance:,.2f}")

    st.divider()
    st.subheader("History")
    st.dataframe(balances, use_container_width=True)

    if len(balances):
        chart_df = balances.copy()
        chart_df["month"] = pd.to_datetime(chart_df["month"])
        chart_df = chart_df.set_index("month")[["balance"]]
        st.line_chart(chart_df)

# ===== Holdings Tab =====
with tab_hld:
    st.subheader("Holdings")
    st.caption("Add tickers, shares, and (optional) cost basis. Prices and dividends are fetched from Yahoo Finance.")

    with st.form("add_holding"):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
        tkr = c1.text_input("Ticker (e.g., AAPL, SPY)").upper().strip()
        sh = c2.number_input("Shares", min_value=0.0, value=0.0, step=0.01, format="%.2f")
        cb = c3.number_input("Cost Basis ($/share)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
        nt = c4.text_input("Note")
        submitted = st.form_submit_button("Add / Update")

    if submitted and tkr:
        if len(holdings) and (holdings["ticker"] == tkr).any():
            holdings.loc[holdings["ticker"] == tkr, ["shares", "cost_basis", "note"]] = [sh, cb, nt]
        else:
            holdings = pd.concat([holdings, pd.DataFrame([{"ticker": tkr, "shares": sh, "cost_basis": cb, "note": nt}])],
                                 ignore_index=True)
        save_csv(DATA_HLD, holdings)
        st.success(f"Saved {tkr}")

    if len(holdings):
        # fetch prices safely
        prices = {}
        for t in holdings["ticker"].tolist():
            if t:
                prices[t] = fetch_quote(t)

        hd = holdings.copy()
        hd["last_price"] = hd["ticker"].map(prices)
        hd["market_value"] = (hd["last_price"].fillna(0.0) * hd["shares"]).round(2)
        hd["unrealized_pnl"] = ((hd["last_price"].fillna(0.0) - hd["cost_basis"]) * hd["shares"]).round(2)
        st.dataframe(hd, use_container_width=True)

        total_val = float(hd["market_value"].sum())
        st.metric("Total Market Value", f"${total_val:,.2f}")

        if st.button("Use Total Market Value as This Month's Balance"):
            m1 = dt.date.today().replace(day=1)
            if len(balances) and (balances["month"] == m1).any():
                balances.loc[balances["month"] == m1, "balance"] = total_val
            else:
                balances = pd.concat([balances, pd.DataFrame([{
                    "month": m1, "balance": total_val, "contribution": 0.0, "note": "Synced from holdings"
                }])], ignore_index=True)
            balances = balances.sort_values("month")
            save_csv(DATA_BAL, balances)
            st.success(f"Updated {m1.isoformat()} to ${total_val:,.2f}")

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
                    st.dataframe(divs.tail(20), use_container_width=True)

    st.divider()
    st.subheader("Delete a Holding")
    if len(holdings):
        del_t = st.selectbox("Choose ticker to delete", [""] + holdings["ticker"].tolist())
        if st.button("Delete", disabled=(del_t == "")) and del_t:
            holdings = holdings[holdings["ticker"] != del_t]
            save_csv(DATA_HLD, holdings)
            st.warning(f"Deleted {del_t}")

# ===== Dashboard Tab =====
with tab_dash:
    st.subheader("Overview")
    total_value = 0.0
    if len(holdings):
        prices = {t: fetch_quote(t) for t in holdings["ticker"].tolist()}
        he = holdings.copy()
        he["last_price"] = he["ticker"].map(prices)
        he["market_value"] = he["last_price"].fillna(0.0) * he["shares"]
        total_value = float(he["market_value"].sum())

    net_contrib = float(balances["contribution"].sum()) if len(balances) else 0.0
    current_balance = float(balances["balance"].iloc[-1]) if len(balances) else total_value
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
        st.info("Add monthly balances to see the chart.")

st.caption("Data stored in local CSV under /data. For private use only.")
