"""
e-rachun - DodoIs — Streamlit Web UI
Main application with authentication, invoice list, search, PDF viewer.
"""

import os
import sys
import base64
import streamlit as st
import pandas as pd
import yaml
import bcrypt
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.db.models import Invoice, SyncLog, init_db, get_engine, get_session_factory
from app.core.config_loader import load_config, get_database_url, get_storage_config, is_dodois_supplier
from app.core.ubl_parser import parse_ubl_xml

# ============================================================
# Page config
# ============================================================
st.set_page_config(
    page_title="eRačun Portal",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# Custom CSS — Design System
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

:root {
    --color-primary: #1E3A5F;
    --color-secondary: #2563EB;
    --color-accent: #059669;
    --color-background: #F8FAFC;
    --color-foreground: #0F172A;
    --color-muted: #64748B;
    --color-border: #E2E8F0;
    --color-destructive: #DC2626;
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: #1E3A5F;
}
section[data-testid="stSidebar"] * {
    color: #E2E8F0 !important;
}
section[data-testid="stSidebar"] .stButton > button,
section[data-testid="stSidebar"] .stButton > button *,
section[data-testid="stSidebar"] .stButton > button p {
    color: #1E3A5F !important;
    background-color: #E2E8F0 !important;
    border: none !important;
}
section[data-testid="stSidebar"] .stButton > button:hover,
section[data-testid="stSidebar"] .stButton > button:hover *,
section[data-testid="stSidebar"] .stButton > button:hover p {
    background-color: #FFFFFF !important;
}
section[data-testid="stSidebar"] .stRadio label:hover {
    background-color: rgba(255,255,255,0.08);
    border-radius: 6px;
}

/* Metrics */
[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 16px 20px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
[data-testid="stMetricLabel"] {
    color: #64748B !important;
    font-size: 0.8rem !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
[data-testid="stMetricValue"] {
    color: #0F172A !important;
    font-weight: 600 !important;
}

/* Buttons */
.stButton > button {
    border-radius: 6px;
    font-weight: 500;
    transition: all 150ms ease;
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(37,99,235,0.15);
}

/* Data table */
[data-testid="stDataFrame"] {
    border-radius: 8px;
    overflow: hidden;
}

/* Login card */
.login-card {
    background: #FFFFFF;
    border-radius: 12px;
    padding: 2.5rem;
    box-shadow: 0 4px 24px rgba(0,0,0,0.06);
    border: 1px solid #E2E8F0;
    max-width: 400px;
    margin: 0 auto;
}
.login-header {
    text-align: center;
    padding-bottom: 1.5rem;
}
.login-header h1 {
    color: #1E3A5F;
    font-size: 1.5rem;
    font-weight: 700;
    margin-bottom: 0.25rem;
}
.login-header p {
    color: #64748B;
    font-size: 0.875rem;
}

/* Page titles */
h1 {
    color: #1E3A5F !important;
    font-weight: 700 !important;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# Config & DB
# ============================================================
@st.cache_resource
def get_config():
    return load_config()


@st.cache_resource
def get_db():
    cfg = get_config()
    db_url = get_database_url(cfg)
    engine = init_db(db_url)
    return get_session_factory(engine)


# ============================================================
# Authentication
# ============================================================
def authenticate():
    """Simple authentication using config.yaml users."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.username = None
        st.session_state.user_role = None

    if st.session_state.authenticated:
        return True

    cfg = get_config()
    users = cfg.get("users", {})

    st.markdown(
        """
        <div class="login-card">
            <div class="login-header">
                <h1>eRačun Portal</h1>
                <p>Invoice management for Orange food business d.o.o.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        with st.form("login_form"):
            username = st.text_input("Username", placeholder="Enter username")
            password = st.text_input("Password", type="password", placeholder="Enter password")
            submit = st.form_submit_button("Sign in", use_container_width=True)

            if submit:
                if username in users:
                    stored_hash = users[username].get("password", "")
                    if bcrypt.checkpw(
                        password.encode("utf-8"), stored_hash.encode("utf-8")
                    ):
                        st.session_state.authenticated = True
                        st.session_state.username = username
                        st.session_state.user_role = users[username].get("role", "viewer")
                        st.session_state.user_name = users[username].get("name", username)
                        st.rerun()
                    else:
                        st.error("Invalid password")
                else:
                    st.error("User not found")

    return False


# ============================================================
# Sidebar
# ============================================================
def render_sidebar():
    with st.sidebar:
        st.markdown(f"### {st.session_state.user_name}")
        st.caption(f"{st.session_state.user_role.upper()}")
        st.divider()

        page = st.radio(
            "Navigation",
            ["Invoices", "Upload XML", "Settings"],
            label_visibility="collapsed",
        )

        st.divider()

        # Sync button (admin only)
        if st.session_state.user_role == "admin":
            if st.button("Sync from eRačun", use_container_width=True):
                sync_invoices()

        # Last sync info
        session = get_db()()
        last_sync = (
            session.query(SyncLog)
            .order_by(SyncLog.started_at.desc())
            .first()
        )
        if last_sync:
            st.caption(f"Last sync: {last_sync.started_at:%Y-%m-%d %H:%M}")
            st.caption(f"Status: {last_sync.status}")
        session.close()

        st.divider()
        if st.button("Sign out", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.username = None
            st.rerun()

    return page


# ============================================================
# Invoice List Page
# ============================================================
def render_invoices_page():
    st.title("Incoming Invoices")

    session = get_db()()

    # ---- Filters ----
    col1, col2, col3, col4 = st.columns([2, 2, 2, 2])

    with col1:
        search_text = st.text_input(
            "Search",
            placeholder="Supplier name or invoice number...",
        )
    with col2:
        # Get unique supplier names
        suppliers = [
            r[0]
            for r in session.query(Invoice.sender_name)
            .distinct()
            .order_by(Invoice.sender_name)
            .all()
        ]
        supplier_filter = st.selectbox(
            "Supplier",
            ["All"] + suppliers,
        )
    with col3:
        date_range = st.date_input(
            "Date range",
            value=(
                datetime.now() - timedelta(days=30),
                datetime.now(),
            ),
        )
    with col4:
        amount_range = st.slider(
            "Amount (EUR)",
            min_value=0.0,
            max_value=10000.0,
            value=(0.0, 10000.0),
            step=10.0,
        )

    # ---- Query ----
    query = session.query(Invoice).filter(Invoice.processing_status != "deleted")

    if search_text:
        search_pattern = f"%{search_text}%"
        query = query.filter(
            (Invoice.sender_name.ilike(search_pattern))
            | (Invoice.invoice_number.ilike(search_pattern))
            | (Invoice.document_nr.ilike(search_pattern))
        )

    if supplier_filter != "All":
        query = query.filter(Invoice.sender_name == supplier_filter)

    if isinstance(date_range, tuple) and len(date_range) == 2:
        query = query.filter(
            Invoice.issue_date >= datetime.combine(date_range[0], datetime.min.time()),
            Invoice.issue_date <= datetime.combine(date_range[1], datetime.max.time()),
        )

    query = query.filter(
        Invoice.total_with_vat >= amount_range[0],
        Invoice.total_with_vat <= amount_range[1],
    )

    invoices = query.order_by(Invoice.issue_date.desc()).all()

    # ---- Summary ----
    total_amount = sum(i.total_with_vat for i in invoices)
    col1, col2, col3 = st.columns(3)
    col1.metric("Invoices", len(invoices))
    col2.metric("Total (with VAT)", f"€{total_amount:,.2f}")
    col3.metric("Suppliers", len(set(i.sender_name for i in invoices)))

    st.divider()

    # ---- Table ----
    if not invoices:
        st.info("No invoices found. Try adjusting filters or sync from eRačun.")
        session.close()
        return

    # Build DataFrame
    cfg = get_config()
    inv_ids = []
    data = []
    for inv in invoices:
        inv_ids.append(inv.id)
        data.append({
            "Date": inv.issue_date.strftime("%d.%m.%Y") if inv.issue_date else "-",
            "Supplier": inv.sender_name,
            "Invoice #": inv.invoice_number or inv.document_nr,
            "Amount (no VAT)": inv.total_without_vat,
            "VAT": inv.total_vat,
            "Total": inv.total_with_vat,
        })

    df = pd.DataFrame(data)

    # Interactive table — select row via checkbox to see details below
    st.caption("\U0001F441 Select a row to preview the invoice")
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Amount (no VAT)": st.column_config.NumberColumn(format="€%.2f"),
            "VAT": st.column_config.NumberColumn(format="€%.2f"),
            "Total": st.column_config.NumberColumn(format="€%.2f"),
        },
    )

    # ---- Detail / PDF viewer ----
    if event and event.selection and event.selection.rows:
        row_idx = event.selection.rows[0]
        inv_id = inv_ids[row_idx]
        inv = session.query(Invoice).get(inv_id)

        if inv:
            st.divider()
            render_invoice_detail(inv)

    session.close()


def render_invoice_detail(inv: Invoice):
    """Show invoice details and PDF preview."""
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader(f"Invoice: {inv.invoice_number}")
        st.markdown(f"""
        **Supplier:** {inv.sender_name}
        **OIB:** {inv.sender_oib}
        **Date:** {inv.issue_date.strftime('%d.%m.%Y') if inv.issue_date else '-'}
        **Due date:** {inv.due_date.strftime('%d.%m.%Y') if inv.due_date else '-'}

        **Amount (no VAT):** €{inv.total_without_vat:,.2f}
        **VAT:** €{inv.total_vat:,.2f}
        **Total:** €{inv.total_with_vat:,.2f}

        **eRačun ID:** {inv.electronic_id or 'Manual upload'}
        **Status:** {inv.processing_status}
        """)

        # Download buttons
        cfg = get_config()
        storage = get_storage_config(cfg)

        if inv.pdf_path:
            pdf_full = Path(storage.get("pdf_dir", "/app/data/pdfs")) / inv.pdf_path
            if pdf_full.exists():
                st.download_button(
                    "Download PDF",
                    data=pdf_full.read_bytes(),
                    file_name=inv.pdf_path,
                    mime="application/pdf",
                    use_container_width=True,
                )

        if inv.xml_path:
            xml_full = Path(storage.get("xml_dir", "/app/data/xmls")) / inv.xml_path
            if xml_full.exists():
                st.download_button(
                    "Download XML",
                    data=xml_full.read_bytes(),
                    file_name=inv.xml_path,
                    mime="application/xml",
                    use_container_width=True,
                )

    with col2:
        # PDF preview in iframe
        if inv.pdf_path:
            cfg = get_config()
            storage = get_storage_config(cfg)
            pdf_full = Path(storage.get("pdf_dir", "/app/data/pdfs")) / inv.pdf_path
            if pdf_full.exists():
                pdf_b64 = base64.b64encode(pdf_full.read_bytes()).decode()
                st.markdown(
                    f'<iframe src="data:application/pdf;base64,{pdf_b64}" '
                    f'width="100%" height="600" type="application/pdf"></iframe>',
                    unsafe_allow_html=True,
                )
            else:
                st.warning("PDF file not found on disk.")
        else:
            st.info("No PDF attached to this invoice.")


# ============================================================
# Upload Page
# ============================================================
def render_upload_page():
    st.title("Upload Invoice XML")
    st.caption("Upload a UBL 2.1 XML invoice file manually.")

    uploaded = st.file_uploader(
        "Drop XML file here",
        type=["xml"],
        accept_multiple_files=True,
    )

    if uploaded:
        session = get_db()()
        cfg = get_config()
        storage = get_storage_config(cfg)

        for f in uploaded:
            st.markdown(f"**Processing:** {f.name}")
            try:
                xml_content = f.read().decode("utf-8", errors="replace")
                ubl = parse_ubl_xml(xml_content)

                # Check duplicate
                existing = (
                    session.query(Invoice)
                    .filter(
                        Invoice.invoice_number == ubl.invoice_number,
                        Invoice.sender_oib == ubl.supplier_oib,
                    )
                    .first()
                )
                if existing:
                    st.warning(
                        f"Duplicate: {ubl.invoice_number} from {ubl.supplier_name} "
                        f"already exists (ID: {existing.id})"
                    )
                    continue

                # Create invoice
                invoice = Invoice(
                    electronic_id=None,
                    document_nr=ubl.invoice_number,
                    sender_oib=ubl.supplier_oib,
                    sender_name=ubl.supplier_name,
                    invoice_number=ubl.invoice_number,
                    issue_date=ubl.issue_date,
                    due_date=ubl.due_date,
                    currency_code=ubl.currency_code,
                    total_without_vat=ubl.total_without_vat,
                    total_vat=ubl.total_vat,
                    total_with_vat=ubl.total_with_vat,
                    processing_status="parsed",
                    dodois_pizzeria=ubl.delivery_pizzeria,
                )

                # Save XML
                safe_name = ubl.invoice_number.replace("/", "_").replace("\\", "_")
                xml_filename = f"manual_{safe_name}.xml"
                xml_dir = Path(storage.get("xml_dir", "/app/data/xmls"))
                xml_dir.mkdir(parents=True, exist_ok=True)
                (xml_dir / xml_filename).write_text(xml_content, encoding="utf-8")
                invoice.xml_path = xml_filename

                # Extract PDF
                if ubl.embedded_pdf_b64:
                    pdf_filename = f"manual_{safe_name}.pdf"
                    pdf_dir = Path(storage.get("pdf_dir", "/app/data/pdfs"))
                    pdf_dir.mkdir(parents=True, exist_ok=True)
                    pdf_bytes = base64.b64decode(ubl.embedded_pdf_b64)
                    (pdf_dir / pdf_filename).write_bytes(pdf_bytes)
                    invoice.pdf_path = pdf_filename

                session.add(invoice)
                session.commit()

                st.success(
                    f"Imported: {ubl.invoice_number} from {ubl.supplier_name} "
                    f"— €{ubl.total_with_vat:,.2f}"
                )

            except Exception as e:
                st.error(f"Error processing {f.name}: {e}")

        session.close()


# ============================================================
# Settings Page
# ============================================================
def render_settings_page():
    st.title("Settings")

    if st.session_state.user_role != "admin":
        st.warning("Admin access required.")
        return

    cfg = get_config()

    # eRačun connection test
    st.subheader("eRačun Connection")
    eracun_cfg = cfg.get("eracun", {})
    connected = bool(eracun_cfg.get("username"))

    if connected:
        st.success(f"Configured: {eracun_cfg.get('username')} / Company: {eracun_cfg.get('company_id')}")
        if st.button("Test Connection"):
            from app.core.eracun_client import EracunClient, EracunCredentials
            creds = EracunCredentials(
                username=eracun_cfg["username"],
                password=eracun_cfg["password"],
                company_id=eracun_cfg["company_id"],
                software_id=eracun_cfg["software_id"],
                company_bu=eracun_cfg.get("company_bu", ""),
            )
            client = EracunClient(eracun_cfg["base_url"], creds)
            if client.ping():
                st.success("Connection OK!")
            else:
                st.error("Connection failed.")
            client.close()
    else:
        st.warning("eRačun credentials not configured in config.yaml")

    st.divider()

    # Sync log
    st.subheader("Sync History")
    session = get_db()()
    logs = (
        session.query(SyncLog)
        .order_by(SyncLog.started_at.desc())
        .limit(20)
        .all()
    )
    if logs:
        log_data = []
        for log in logs:
            log_data.append({
                "Started": log.started_at.strftime("%Y-%m-%d %H:%M") if log.started_at else "-",
                "Status": log.status,
                "Found": log.invoices_found,
                "New": log.invoices_new,
                "Error": log.error_message or "-",
            })
        st.dataframe(pd.DataFrame(log_data), use_container_width=True, hide_index=True)
    else:
        st.info("No sync history yet.")

    session.close()

    st.divider()

    # DB stats
    st.subheader("Database Stats")
    session = get_db()()
    total = session.query(Invoice).count()
    with_pdf = session.query(Invoice).filter(Invoice.pdf_path.isnot(None)).count()
    st.metric("Total invoices", total)
    st.metric("With PDF", with_pdf)
    session.close()


# ============================================================
# Sync function
# ============================================================
def sync_invoices():
    """Trigger sync from eRačun."""
    cfg = get_config()
    eracun_cfg = cfg.get("eracun", {})

    if not eracun_cfg.get("username"):
        st.error("eRačun not configured!")
        return

    from app.core.eracun_client import EracunClient, EracunCredentials
    from app.core.invoice_sync import InvoiceSyncService

    creds = EracunCredentials(
        username=eracun_cfg["username"],
        password=eracun_cfg["password"],
        company_id=eracun_cfg["company_id"],
        software_id=eracun_cfg["software_id"],
        company_bu=eracun_cfg.get("company_bu", ""),
    )

    storage = get_storage_config(cfg)

    with st.spinner("Syncing from eRačun..."):
        client = EracunClient(eracun_cfg["base_url"], creds)
        sync_service = InvoiceSyncService(
            eracun_client=client,
            session_factory=get_db(),
            pdf_dir=storage.get("pdf_dir", "/app/data/pdfs"),
            xml_dir=storage.get("xml_dir", "/app/data/xmls"),
        )
        result = sync_service.sync(
            lookback_days=cfg.get("sync", {}).get("lookback_days", 90)
        )
        client.close()

    if result["status"] == "success":
        st.success(f"Sync complete: {result['new']} new invoices (of {result['found']} total)")
    else:
        st.error(f"Sync error: {result.get('error', 'Unknown')}")


# ============================================================
# Main
# ============================================================
def main():
    if not authenticate():
        return

    page = render_sidebar()

    if page == "Invoices":
        render_invoices_page()
    elif page == "Upload XML":
        render_upload_page()
    elif page == "Settings":
        render_settings_page()


if __name__ == "__main__":
    main()
else:
    main()
