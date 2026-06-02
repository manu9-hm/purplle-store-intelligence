import streamlit as st
import plotly.graph_objects as go
import requests
import pandas as pd
import time
from datetime import datetime

# Configuration
API_BASE = "http://127.0.0.1:8000"
REFRESH_RATE = 5  # Seconds

st.set_page_config(
    page_title="Purplle Store Intelligence",
    page_icon="💜",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Custom Professional Styling (Dark Theme / Datadog-esque)
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #fafafa; }
    [data-testid="metric-container"] {
        background-color: #161b22;
        border: 1px solid #30363d;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.3);
    }
    [data-testid="stMetricValue"] { color: #bb86fc; font-weight: bold; }
    .anomaly-container {
        background-color: #161b22;
        padding: 15px;
        border-radius: 8px;
        border: 1px solid #30363d;
        margin-bottom: 10px;
    }
    </style>
""", unsafe_allow_html=True)

def fetch_api(endpoint):
    try:
        response = requests.get(f"{API_BASE}{endpoint}", timeout=2)
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None

def run_dashboard():
    # Header section
    st.title("💜 Purplle Store Intelligence Dashboard")
    st.markdown("#### Real-Time Retail Analytics & Operations Monitoring")
    st.divider()

    # API Status Check
    health = fetch_api("/health")
    if not health:
        st.error("🚨 **API CONNECTION LOST**: The dashboard is unable to reach the Intelligence API. Attempting to reconnect...")
        st.info("Check if the backend is running at http://127.0.0.1:8000")
        time.sleep(REFRESH_RATE)
        st.rerun()

    stores = health.get("stores_seen", [])
    if not stores:
        st.warning("📡 **WAITING FOR DATA**: API is online but no store data has been ingested yet.")
        time.sleep(REFRESH_RATE)
        st.rerun()

    # Store Selector (if multiple exist)
    store_id = stores[0]
    if len(stores) > 1:
        store_id = st.sidebar.selectbox("Select Store", stores)

    # Fetch Core Data
    metrics = fetch_api(f"/stores/{store_id}/metrics")
    funnel = fetch_api(f"/stores/{store_id}/funnel")
    heatmap = fetch_api(f"/stores/{store_id}/heatmap")
    anomalies = fetch_api(f"/stores/{store_id}/anomalies")

    # ROW 1: KPI CARDS
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    if metrics:
        kpi1.metric("Unique Visitors", metrics.get("unique_visitors", 0))
        kpi2.metric("Conversion Rate", f"{metrics.get('conversion_rate', 0)*100:.1f}%")
        kpi3.metric("Current Queue Depth", metrics.get("queue_depth", 0))
        
        anomaly_count = len(anomalies.get("anomalies", [])) if anomalies else 0
        kpi4.metric("Active Anomalies", anomaly_count, delta=anomaly_count, delta_color="inverse" if anomaly_count > 0 else "normal")

    st.write("") # Spacing

    # ROW 2 & 3: Charts
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("🛒 Customer Journey Funnel")
        if funnel:
            fig_funnel = go.Figure(go.Funnel(
                y=["Entry", "Zone Visit", "Billing Queue", "Purchase"],
                x=[funnel['entry_count'], funnel['zone_visit_count'], funnel['billing_queue_count'], funnel['purchase_count']],
                textinfo="value+percent initial",
                marker={"color": ["#4527a0", "#5e35b1", "#7e57c2", "#b39ddb"]}
            ))
            fig_funnel.update_layout(template="plotly_dark", height=400, margin=dict(l=40, r=40, b=20, t=20))
            st.plotly_chart(fig_funnel, use_container_width=True)

    with col_right:
        st.subheader("📍 Zone Intensity Heatmap")
        if heatmap:
            zones_df = pd.DataFrame(heatmap.get("zones", []))
            if not zones_df.empty:
                fig_hm = go.Figure(data=go.Bar(
                    x=zones_df['zone_id'],
                    y=zones_df['normalized_score'],
                    marker_color=zones_df['normalized_score'],
                    marker_colorscale='Purp',
                    text=zones_df['visit_frequency'],
                    hovertext=zones_df['avg_dwell_ms'].apply(lambda x: f"Avg Dwell: {x/1000:.1f}s")
                ))
                fig_hm.update_layout(template="plotly_dark", height=400, yaxis_title="Normalized Score (0-100)", xaxis_title="Store Zone")
                st.plotly_chart(fig_hm, use_container_width=True)

    # ROW 4: ANOMALIES
    st.subheader("🚨 Operations & Anomaly Panel")
    if anomalies and anomalies.get("anomalies"):
        for anomaly in anomalies["anomalies"]:
            severity = anomaly.get("severity", "INFO")
            color = "#ff4b4b" if severity == "CRITICAL" else "#ffa500" if severity == "WARN" else "#0080ff"
            st.markdown(f"""
                <div class="anomaly-container" style="border-left: 5px solid {color};">
                    <strong style="color: {color};">{severity}</strong> | 
                    <strong>{anomaly['type'].replace('_', ' ').upper()}</strong><br/>
                    <span style="color: #8b949e;">Suggested Action: {anomaly['suggested_action']}</span>
                </div>
            """, unsafe_allow_html=True)
    else:
        st.success("✅ System Status: Healthy. No active operational anomalies detected.")

    # Auto-refresh logic
    st.write(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(REFRESH_RATE)
    st.rerun()

if __name__ == "__main__":
    try:
        run_dashboard()
    except Exception as e:
        st.error(f"Dashboard Error: {e}")