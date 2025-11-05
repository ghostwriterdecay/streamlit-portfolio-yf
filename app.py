
import streamlit as st
import pandas as pd
import yfinance as yf
import datetime as dt
from pathlib import Path

st.set_page_config(page_title="Private Portfolio Tracker", page_icon="📈", layout="wide")

PASSCODE = st.secrets.get("passcode", None)

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

DATA_BAL = Path("data/balances.csv")
DATA_HLD = Path("data/holdings.csv")
DATA_BAL.parent.mkdir(parents=True, exist_ok=True)

@st.cache_data(ttl=300)
def fetch_quote(ticker: str):
    try:
        t = yf.Ticker(ticker)
        px = None
        try:
            fi = getattr(t, "fast_info", None)
            if fi is not None and "lastPrice" in fi:
                px = fi["lastPrice"]
        except Exception:
            px = None
        if px is None:
            hist = t.history(period="1d")
            if not hist.empty:
                px = float(hist["Close"].iloc[-1])
        return px
    except Exception:
        return None

@st.cache_data(ttl=1800)
def fetch_dividends(ticker: str):
    try:
        t = yf.Ticker(ticker)
        div = t.dividends
        if div is None or len(div) == 0:
            return pd.DataFrame(columns=["Date","Dividend"])
        df = div.reset_index()
        df.columns = ["Date","Dividend"]
        return df
    except Exception:
        return pd.DataFrame(columns=["Date","Dividend"])

def load_csv(path: Path, cols):
    if path.exists():
        df = pd.read_csv(path)
        for c, default in cols.items():
            if c not in df.columns:
                df[c] = default
        return df
    else:
        return pd.DataFrame([{k:v for k,v in cols.items()}]).head(0)

def save_csv(path: Path, df: pd.DataFrame):
    df.to_csv(path, index=False)

bal_cols = {"month":"", "balance":0.0, "contribution":0.0, "note":""}
hld_cols = {"ticker":"", "shares":0.0, "cost_basis":0.0, "note":""}

balances = load_csv(DATA_BAL, bal_cols)
holdings = load_csv(DATA_HLD, hld_cols)

if "month" in balances.columns and len(balances):
    balances["month"] = pd.to_datetime(balances["month"]).dt.date
for c in ["balance","contribution"]:
    if c in balances.columns:
        balances[c] = pd.to_numeric(balances[c], errors="coerce").fillna(0.0)

for c in ["shares","cost_basis"]:
    if c in holdings.columns:
        holdings[c] = pd.to_numeric(holdings[c], errors="coerce").fillna(0.0)
holdings["ticker"] = holdings["ticker"].astype(str).str.upper()

st.title("📈 Private Portfolio Tracker")
st.caption("Single-user passcode, monthly balance updates, equity holdings with live Yahoo Finance prices.")

tab_bal, tab_hld, tab_dash = st.tabs(["📅 Monthly Balance", "🧺 Holdings", "📊 Dashboard"])

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
    sync = st.checkbox("Sync balance from current holdings value", value=False)
    if st.button("Save/Update Month", use_container_width=True):
        if sync:
            total = 0.0
            for _, r in holdings.iterrows():
                if r["ticker"] and r["shares"] > 0:
                    px = fetch_quote(r["ticker"])
                    if px:
                        total += float(px) * float(r["shares"])
            balance = total
        m1 = month.replace(day=1)
        if (len(balances) and (balances["month"] == m1).any()):
            balances.loc[balances["month"] == m1, ["balance","contribution","note"]] = [balance, contrib, note]
        else:
            balances = pd.concat([balances, pd.DataFrame([{"month": m1, "balance": balance, "contribution": contrib, "note": note}])], ignore_index=True)
        balances = balances.sort_values("month")
        save_csv(DATA_BAL, balances)
        st.success(f"Saved {m1.isoformat()} → ${balance:,.2f}")

    st.divider()
    st.subheader("History")
    st.dataframe(balances.style.format({"balance":"${:,.2f}","contribution":"${:,.2f}"}), use_container_width=True)

    if len(balances):
        chart_df = balances.copy()
        chart_df["month"] = pd.to_datetime(chart_df["month"])
        chart_df = chart_df.set_index("month")[["balance"]]
        st.line_chart(chart_df)

with tab_hld:
    st.subheader("Holdings")
    with st.form("add_holding"):
        c1, c2, c3, c4 = st.columns([2,1,1,2])
        tkr = c1.text_input("Ticker (e.g., AAPL, SPY)").upper().strip()
        sh  = c2.number_input("Shares", min_value=0.0, value=0.0, step=1.0)
        cb  = c3.number_input("Cost Basis ($/share)", min_value=0.0, value=0.0, step=1.0)
        nt  = c4.text_input("Note")
        submitted = st.form_submit_button("Add / Update")
    if submitted and tkr:
        if (holdings["ticker"] == tkr).any():
            holdings.loc[holdings["ticker"] == tkr, ["shares","cost_basis","note"]] = [sh, cb, nt]
        else:
            holdings = pd.concat([holdings, pd.DataFrame([{"ticker": tkr, "shares": sh, "cost_basis": cb, "note": nt}])], ignore_index=True)
        save_csv(DATA_HLD, holdings)
        st.success(f"Saved {tkr}")

    if len(holdings):
        prices = {}
        for t in holdings["ticker"].tolist():
            if t:
                prices[t] = fetch_quote(t)
        holdings_display = holdings.copy()
        holdings_display["last_price"] = holdings_display["ticker"].map(prices)
        holdings_display["market_value"] = (holdings_display["last_price"].fillna(0.0) * holdings_display["shares"]).round(2)
        holdings_display["unrealized_pnl"] = ((holdings_display["last_price"].fillna(0.0) - holdings_display["cost_basis"]) * holdings_display["shares"]).round(2)
        st.dataframe(
            holdings_display.style.format({"shares":"{:,.2f}","cost_basis":"${:,.2f}","last_price":"${:,.2f}","market_value":"${:,.2f}","unrealized_pnl":"${:,.2f}"}),
            use_container_width=True
        )

        total_val = float(holdings_display["market_value"].sum())
        st.metric("Total Market Value", f"${total_val:,.2f}")
        if st.button("Use Total Market Value as This Month's Balance"):
            m1 = dt.date.today().replace(day=1)
            if (len(balances) and (balances["month"] == m1).any()):
                balances.loc[balances["month"] == m1, "balance"] = total_val
            else:
                balances = pd.concat([balances, pd.DataFrame([{"month": m1, "balance": total_val, "contribution": 0.0, "note": "Synced from holdings"}])], ignore_index=True)
            balances = balances.sort_values("month")
            save_csv(DATA_BAL, balances)
            st.success(f"Updated {m1.isoformat()} to ${total_val:,.2f}")

        st.divider()
        st.subheader("Dividend Lookup")
        t_sel = st.selectbox("Select ticker for dividend history", holdings["ticker"].tolist())
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
        if st.button("Delete", disabled=(del_t==\"\")) and del_t:
            holdings = holdings[holdings["ticker"] != del_t]
            save_csv(DATA_HLD, holdings)
            st.warning(f"Deleted {del_t}")

with tab_dash:
    st.subheader("Overview")
    if len(holdings):
        prices = {t: fetch_quote(t) for t in holdings["ticker"].tolist()}
        holdings_eval = holdings.copy()
        holdings_eval["last_price"] = holdings_eval["ticker"].map(prices)
        holdings_eval["market_value"] = holdings_eval["last_price"].fillna(0.0) * holdings_eval["shares"]
        total_value = float(holdings_eval["market_value"].sum())
    else:
        total_value = 0.0

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
