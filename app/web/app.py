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

from app.db.models import (
    Invoice, SyncLog, DodoisSupplierCatalog, SupplierMapping,
    DodoisRawMaterialCatalog, ProductMapping,
    init_db, get_engine, get_session_factory,
    get_or_create_supplier_mapping, sync_product_mappings_from_lines, seed_all,
)
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
    session_factory = get_session_factory(engine)
    # Seed catalog tables from config.yaml on first run
    session = session_factory()
    try:
        seed_all(session, cfg)
    finally:
        session.close()
    return session_factory


# ============================================================
# Authentication
# ============================================================
def _do_login():
    """Callback: runs BEFORE widget rendering on rerun, preventing form flash."""
    cfg = get_config()
    users = cfg.get("users", {})
    username = st.session_state.get("_login_user", "")
    password = st.session_state.get("_login_pass", "")

    if username in users:
        stored_hash = users[username].get("password", "")
        if bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
            st.session_state.authenticated = True
            st.session_state.username = username
            st.session_state.user_role = users[username].get("role", "viewer")
            st.session_state.user_name = users[username].get("name", username)
            st.session_state._login_error = None
        else:
            st.session_state._login_error = "Invalid password"
    else:
        st.session_state._login_error = "User not found"


def authenticate():
    """Simple authentication using config.yaml users."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.username = None
        st.session_state.user_role = None

    if st.session_state.authenticated:
        return True

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
        st.text_input("Username", placeholder="Enter username", key="_login_user")
        st.text_input("Password", type="password", placeholder="Enter password", key="_login_pass")
        st.button("Sign in", use_container_width=True, on_click=_do_login)

        if st.session_state.get("_login_error"):
            st.error(st.session_state._login_error)

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
            ["Invoices", "Upload XML", "Mappings", "Settings"],
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
    session = get_db()()

    # ---- Filters (compact single row) ----
    suppliers = [
        r[0]
        for r in session.query(Invoice.sender_name)
        .distinct()
        .order_by(Invoice.sender_name)
        .all()
    ]

    f1, f2, f3 = st.columns([3, 2, 2])
    with f1:
        search_text = st.text_input(
            "Search",
            placeholder="Supplier or invoice #...",
            label_visibility="collapsed",
        )
    with f2:
        supplier_filter = st.selectbox(
            "Supplier",
            ["All suppliers"] + suppliers,
            label_visibility="collapsed",
        )
    with f3:
        date_range = st.date_input(
            "Date range",
            value=(
                datetime.now() - timedelta(days=30),
                datetime.now(),
            ),
            format="DD.MM.YYYY",
            label_visibility="collapsed",
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

    if supplier_filter != "All suppliers":
        query = query.filter(Invoice.sender_name == supplier_filter)

    if isinstance(date_range, tuple) and len(date_range) == 2:
        query = query.filter(
            Invoice.issue_date >= datetime.combine(date_range[0], datetime.min.time()),
            Invoice.issue_date <= datetime.combine(date_range[1], datetime.max.time()),
        )

    invoices = query.order_by(Invoice.issue_date.desc()).all()

    # ---- Summary (compact inline) ----
    total_amount = sum(i.total_with_vat for i in invoices)
    n_suppliers = len(set(i.sender_name for i in invoices))
    st.markdown(
        f"**{len(invoices)}** invoices &nbsp;/&nbsp; **{n_suppliers}** suppliers "
        f"&nbsp;/&nbsp; Total: **€{total_amount:,.2f}**",
    )

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
            "Pizzeria": inv.dodois_pizzeria or "—",
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

            # Pizzeria selector
            cfg = get_config()
            pizzerias = cfg.get("dodois", {}).get("pizzerias", {})
            pizzeria_names = ["—"] + [v.get("name", k) for k, v in pizzerias.items()]
            current = inv.dodois_pizzeria or "—"
            current_idx = pizzeria_names.index(current) if current in pizzeria_names else 0

            selected_pizzeria = st.selectbox(
                "Pizzeria",
                pizzeria_names,
                index=current_idx,
                key=f"pizzeria_{inv.id}",
            )

            new_value = None if selected_pizzeria == "—" else selected_pizzeria
            if new_value != inv.dodois_pizzeria:
                inv.dodois_pizzeria = new_value
                session.commit()
                st.rerun()

            render_invoice_detail(inv)

    session.close()


def _delete_invoice(inv: Invoice, storage: dict):
    """Soft-delete invoice and remove associated files from disk."""
    session = get_db()()
    db_inv = session.query(Invoice).get(inv.id)
    if db_inv:
        db_inv.processing_status = "deleted"
        session.commit()
    session.close()

    if inv.pdf_path:
        pdf_full = Path(storage.get("pdf_dir", "/app/data/pdfs")) / inv.pdf_path
        pdf_full.unlink(missing_ok=True)
    if inv.xml_path:
        xml_full = Path(storage.get("xml_dir", "/app/data/xmls")) / inv.xml_path
        xml_full.unlink(missing_ok=True)

    st.success(f"Invoice {inv.invoice_number} deleted.")


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

        # Delete invoice (admin only)
        if st.session_state.get("user_role") == "admin":
            st.divider()
            confirm_key = f"confirm_delete_{inv.id}"
            if st.session_state.get(confirm_key):
                st.warning("Are you sure? This cannot be undone.")
                col_yes, col_no = st.columns(2)
                with col_yes:
                    if st.button("Yes, delete", key=f"del_yes_{inv.id}", type="primary", use_container_width=True):
                        _delete_invoice(inv, storage)
                        st.session_state[confirm_key] = False
                        st.rerun()
                with col_no:
                    if st.button("Cancel", key=f"del_no_{inv.id}", use_container_width=True):
                        st.session_state[confirm_key] = False
                        st.rerun()
            else:
                if st.button("Delete invoice", key=f"del_{inv.id}", type="secondary", use_container_width=True):
                    st.session_state[confirm_key] = True
                    st.rerun()

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

                # Ensure supplier mapping row exists and auto-register product lines
                supplier_mapping = get_or_create_supplier_mapping(session, ubl.supplier_oib, ubl.supplier_name)
                if ubl.lines:
                    sync_product_mappings_from_lines(session, supplier_mapping, ubl.lines)

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
# Supplier Mapping section
# ============================================================
def render_supplier_mapping_section(cfg: dict):
    """DB-driven supplier mapping: eRačun suppliers → Dodois suppliers."""
    col_title, col_btn = st.columns([4, 1])
    with col_title:
        st.subheader("Supplier Mapping")
    with col_btn:
        if st.button("Sync Dodois Catalog", use_container_width=True):
            _sync_dodois_catalog(cfg)

    st.caption("Select a row to link a supplier to Dodois and enable upload.")

    session = get_db()()

    # Load all mappings with invoice counts
    mappings = (
        session.query(SupplierMapping)
        .order_by(SupplierMapping.eracun_name)
        .all()
    )

    # Invoice counts per OIB
    from sqlalchemy import func
    counts = dict(
        session.query(Invoice.sender_oib, func.count(Invoice.id))
        .group_by(Invoice.sender_oib)
        .all()
    )

    if not mappings:
        st.info("No suppliers seen yet. Import or sync invoices to populate this list.")
        session.close()
        return

    mapping_ids = []
    rows = []
    for m in mappings:
        mapping_ids.append(m.id)
        dodois_name = m.dodois_supplier.dodois_name if m.dodois_supplier else "—"
        status_map = {"unmapped": "⚠ Unmapped", "disabled": "Off", "enabled": "On"}
        rows.append({
            "Supplier (eRačun)": m.eracun_name,
            "OIB": m.eracun_oib,
            "Dodois supplier": dodois_name,
            "Invoices": counts.get(m.eracun_oib, 0),
            "Status": status_map.get(m.status, m.status),
        })

    df = pd.DataFrame(rows)
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Invoices": st.column_config.NumberColumn(format="%d"),
        },
    )

    if event and event.selection and event.selection.rows:
        row_idx = event.selection.rows[0]
        mapping = session.query(SupplierMapping).get(mapping_ids[row_idx])

        if mapping:
            st.divider()
            st.markdown(f"**{mapping.eracun_name}** · OIB: `{mapping.eracun_oib}`")

            # Dodois supplier selector
            catalog_entries = (
                session.query(DodoisSupplierCatalog)
                .order_by(DodoisSupplierCatalog.dodois_name)
                .all()
            )
            catalog_options = ["— not linked —"] + [c.dodois_name for c in catalog_entries]
            catalog_ids = [None] + [c.id for c in catalog_entries]

            current_catalog_idx = (
                catalog_ids.index(mapping.dodois_catalog_id)
                if mapping.dodois_catalog_id in catalog_ids else 0
            )

            selected_catalog_name = st.selectbox(
                "Dodois supplier",
                catalog_options,
                index=current_catalog_idx,
                key=f"sup_catalog_{mapping.id}",
            )
            new_catalog_id = catalog_ids[catalog_options.index(selected_catalog_name)]

            # Enabled toggle
            new_enabled = st.checkbox(
                "Enable upload to Dodois",
                value=mapping.enabled,
                key=f"sup_enabled_{mapping.id}",
                disabled=(new_catalog_id is None),
            )

            # Auto-save on any change
            changed = (new_catalog_id != mapping.dodois_catalog_id) or (new_enabled != mapping.enabled)
            if changed:
                mapping.dodois_catalog_id = new_catalog_id
                mapping.enabled = new_enabled if new_catalog_id is not None else False
                session.commit()
                st.rerun()

            if mapping.dodois_catalog_id is not None:
                st.info("Supplier linked. Go to the **Products** tab to map invoice lines.")

    session.close()


# ============================================================
# Product Mapping section
# ============================================================
def render_product_mapping_section(session, supplier_mapping: SupplierMapping):
    """Product mapping for a given supplier: eRačun line descriptions → Dodois raw materials."""
    dodois_name = supplier_mapping.dodois_supplier.dodois_name if supplier_mapping.dodois_supplier else "?"
    st.subheader(f"Product Mapping — {dodois_name}")
    st.caption(
        "Map invoice line items to Dodois raw materials. "
        "New products appear here automatically when invoices are imported."
    )

    products = (
        session.query(ProductMapping)
        .filter_by(supplier_mapping_id=supplier_mapping.id)
        .order_by(ProductMapping.eracun_description)
        .all()
    )

    # Add new product mapping row
    with st.expander("Add product mapping"):
        new_desc = st.text_input(
            "eRačun description",
            placeholder="Exact or partial text from invoice line",
            key=f"new_prod_desc_{supplier_mapping.id}",
        )
        new_ean = st.text_input(
            "EAN (optional)",
            placeholder="e.g. 3800023456789",
            key=f"new_prod_ean_{supplier_mapping.id}",
        )
        if st.button("Add", key=f"new_prod_add_{supplier_mapping.id}"):
            if new_desc.strip():
                session.add(ProductMapping(
                    supplier_mapping_id=supplier_mapping.id,
                    eracun_description=new_desc.strip(),
                    eracun_ean=new_ean.strip() or None,
                ))
                session.commit()
                st.rerun()
            else:
                st.warning("Description is required.")

    if not products:
        st.info("No product mappings yet. Add entries above or import invoices.")
        return

    product_ids = []
    prod_rows = []
    for p in products:
        product_ids.append(p.id)
        mat_name = p.raw_material.dodois_name if p.raw_material else "—"
        prod_rows.append({
            "eRačun description": p.eracun_description,
            "EAN": p.eracun_ean or "—",
            "Dodois material": mat_name,
            "Active": "Yes" if p.enabled else "No",
        })

    prod_event = st.dataframe(
        pd.DataFrame(prod_rows),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"prod_table_{supplier_mapping.id}",
    )

    if prod_event and prod_event.selection and prod_event.selection.rows:
        prod_idx = prod_event.selection.rows[0]
        product = session.query(ProductMapping).get(product_ids[prod_idx])

        if product:
            st.markdown(f"**{product.eracun_description}**")

            # Raw material selector (filtered to this supplier's catalog)
            materials = (
                session.query(DodoisRawMaterialCatalog)
                .filter_by(supplier_catalog_id=supplier_mapping.dodois_catalog_id)
                .order_by(DodoisRawMaterialCatalog.dodois_name)
                .all()
            )
            mat_options = ["— not linked —"] + [
                f"{m.dodois_name} ({int(m.container_size)}{_unit_label(m.unit)})" for m in materials
            ]
            mat_ids = [None] + [m.id for m in materials]

            current_mat_idx = (
                mat_ids.index(product.dodois_raw_material_id)
                if product.dodois_raw_material_id in mat_ids else 0
            )

            selected_mat = st.selectbox(
                "Dodois raw material",
                mat_options,
                index=current_mat_idx,
                key=f"prod_mat_{product.id}",
            )
            new_mat_id = mat_ids[mat_options.index(selected_mat)]

            new_prod_enabled = st.checkbox(
                "Active",
                value=product.enabled,
                key=f"prod_enabled_{product.id}",
            )

            col_save, col_del = st.columns([3, 1])
            with col_del:
                if st.button("Delete", key=f"prod_del_{product.id}", type="secondary"):
                    session.delete(product)
                    session.commit()
                    st.rerun()

            prod_changed = (new_mat_id != product.dodois_raw_material_id) or (new_prod_enabled != product.enabled)
            if prod_changed:
                product.dodois_raw_material_id = new_mat_id
                product.enabled = new_prod_enabled
                session.commit()
                st.rerun()


def _unit_label(unit: int) -> str:
    return {1: "pcs", 5: "g", 8: "m"}.get(unit, "?")


# ============================================================
# Dodois catalog sync
# ============================================================
def _sync_dodois_catalog(cfg: dict):
    """Fetch supplier list from Dodois API and update dodois_supplier_catalog table."""
    import requests

    dodois_cfg = cfg.get("dodois", {})
    token = dodois_cfg.get("token", "").strip()
    base_url = dodois_cfg.get("base_url", "https://officemanager.dodois.com")

    if not token:
        st.error("Dodois token not set. Add `dodois.token` to config.yaml (Bearer token from browser DevTools).")
        return

    # Use first configured pizzeria department
    pizzerias = dodois_cfg.get("pizzerias", {})
    department_id = next(
        (p.get("department_id") for p in pizzerias.values() if p.get("department_id")),
        None,
    )
    if not department_id:
        st.error("No department_id configured in dodois.pizzerias.")
        return

    url = f"{base_url}/Accounting/v1/Suppliers?departmentId={department_id}"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        st.error(f"Dodois API error: {e}")
        return

    items = data.get("items", [])
    if not items:
        st.warning("No suppliers returned from Dodois API.")
        return

    session = get_db()()
    now = __import__("datetime").datetime.utcnow()
    added, updated = 0, 0
    for s in items:
        did = s.get("id")
        if not did:
            continue
        entry = session.query(DodoisSupplierCatalog).filter_by(dodois_id=did).first()
        if entry:
            entry.dodois_name = s.get("name", entry.dodois_name)
            entry.dodois_inn = s.get("inn") or entry.dodois_inn
            entry.synced_at = now
            updated += 1
        else:
            session.add(DodoisSupplierCatalog(
                dodois_id=did,
                dodois_name=s.get("name", did),
                dodois_inn=s.get("inn") or None,
                synced_at=now,
            ))
            added += 1
    session.commit()
    session.close()
    st.success(f"Dodois catalog updated: {added} added, {updated} updated.")


# ============================================================
# Mappings Page
# ============================================================
def render_mappings_page():
    st.title("Mappings")

    if st.session_state.user_role != "admin":
        st.warning("Admin access required.")
        return

    tab_sup, tab_prod = st.tabs(["Suppliers", "Products"])

    with tab_sup:
        cfg = get_config()
        render_supplier_mapping_section(cfg)

    with tab_prod:
        render_all_products_tab()


def render_all_products_tab():
    """Products tab: all product mappings across all suppliers, with inline editing."""
    session = get_db()()

    all_supplier_mappings = (
        session.query(SupplierMapping)
        .order_by(SupplierMapping.eracun_name)
        .all()
    )

    if not all_supplier_mappings:
        st.info("No suppliers yet. Import invoices first.")
        session.close()
        return

    # Metrics
    total = session.query(ProductMapping).count()
    mapped = session.query(ProductMapping).filter(
        ProductMapping.dodois_raw_material_id.isnot(None)
    ).count()
    unmapped = total - mapped

    col1, col2, col3 = st.columns(3)
    col1.metric("Total products", total)
    col2.metric("Mapped", mapped)
    col3.metric("Unmapped", unmapped)

    if total == 0:
        st.info("No product entries yet. Import invoices — lines will appear here automatically.")
        session.close()
        return

    st.divider()

    # Supplier filter
    sup_options = ["All suppliers"] + [m.eracun_name for m in all_supplier_mappings]
    sup_filter = st.selectbox("Filter by supplier", sup_options, key="prod_sup_filter")

    # Show only unmapped toggle
    only_unmapped = st.checkbox("Show only unmapped", key="prod_only_unmapped")

    # Build query
    q = session.query(ProductMapping)
    if sup_filter != "All suppliers":
        sm = next((m for m in all_supplier_mappings if m.eracun_name == sup_filter), None)
        if sm:
            q = q.filter_by(supplier_mapping_id=sm.id)
    if only_unmapped:
        q = q.filter(ProductMapping.dodois_raw_material_id.is_(None))
    products = q.order_by(ProductMapping.eracun_description).all()

    if not products:
        st.info("No products match the current filter.")
        session.close()
        return

    # Add product manually
    with st.expander("Add product mapping manually"):
        sup_names = [m.eracun_name for m in all_supplier_mappings]
        man_sup = st.selectbox("Supplier", sup_names, key="man_prod_sup")
        man_desc = st.text_input("eRačun description", placeholder="Exact or partial text from invoice line", key="man_prod_desc")
        man_ean = st.text_input("EAN (optional)", placeholder="e.g. 3800023456789", key="man_prod_ean")
        if st.button("Add", key="man_prod_add"):
            if man_desc.strip():
                sm = next((m for m in all_supplier_mappings if m.eracun_name == man_sup), None)
                if sm:
                    session.add(ProductMapping(
                        supplier_mapping_id=sm.id,
                        eracun_description=man_desc.strip(),
                        eracun_ean=man_ean.strip() or None,
                    ))
                    session.commit()
                    st.rerun()
            else:
                st.warning("Description is required.")

    st.divider()

    # Table
    product_ids = []
    rows = []
    for p in products:
        product_ids.append(p.id)
        sup_name = p.supplier_mapping.eracun_name if p.supplier_mapping else "?"
        mat_name = p.raw_material.dodois_name if p.raw_material else "—"
        rows.append({
            "Supplier": sup_name,
            "eRačun description": p.eracun_description,
            "EAN": p.eracun_ean or "—",
            "Dodois material": mat_name,
            "Active": "Yes" if p.enabled else "No",
        })

    event = st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="all_prod_table",
    )

    if event and event.selection and event.selection.rows:
        prod_idx = event.selection.rows[0]
        product = session.query(ProductMapping).get(product_ids[prod_idx])

        if product:
            st.divider()
            st.markdown(
                f"**{product.eracun_description}**  \n"
                f"Supplier: {product.supplier_mapping.eracun_name if product.supplier_mapping else '?'}"
            )

            sup_mapping = product.supplier_mapping
            if not sup_mapping or not sup_mapping.dodois_catalog_id:
                st.warning("This supplier is not linked to a Dodois supplier yet. Map it in the Suppliers tab first.")
                if st.button("Delete", key=f"allprod_del_nosup_{product.id}", type="secondary"):
                    session.delete(product)
                    session.commit()
                    st.rerun()
                session.close()
                return

            materials = (
                session.query(DodoisRawMaterialCatalog)
                .filter_by(supplier_catalog_id=sup_mapping.dodois_catalog_id)
                .order_by(DodoisRawMaterialCatalog.dodois_name)
                .all()
            )
            mat_options = ["— not linked —"] + [
                f"{m.dodois_name} ({int(m.container_size)}{_unit_label(m.unit)})"
                for m in materials
            ]
            mat_ids = [None] + [m.id for m in materials]

            current_mat_idx = (
                mat_ids.index(product.dodois_raw_material_id)
                if product.dodois_raw_material_id in mat_ids else 0
            )

            col_mat, col_act = st.columns([5, 1])
            with col_mat:
                selected_mat = st.selectbox(
                    "Dodois raw material",
                    mat_options,
                    index=current_mat_idx,
                    key=f"allprod_mat_{product.id}",
                )
            with col_act:
                st.write("")
                new_enabled = st.checkbox("Active", value=product.enabled, key=f"allprod_en_{product.id}")

            new_mat_id = mat_ids[mat_options.index(selected_mat)]

            col_save, col_del = st.columns([4, 1])
            with col_del:
                if st.button("Delete", key=f"allprod_del_{product.id}", type="secondary"):
                    session.delete(product)
                    session.commit()
                    st.rerun()

            if (new_mat_id != product.dodois_raw_material_id) or (new_enabled != product.enabled):
                product.dodois_raw_material_id = new_mat_id
                product.enabled = new_enabled
                session.commit()
                st.rerun()

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
    elif page == "Mappings":
        render_mappings_page()
    elif page == "Settings":
        render_settings_page()


if __name__ == "__main__":
    main()
else:
    main()
