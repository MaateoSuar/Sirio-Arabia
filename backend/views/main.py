from flask import Blueprint, render_template, request, redirect, url_for, jsonify, abort, send_file
from flask import current_app
from datetime import date, datetime, timedelta
from io import BytesIO
import smtplib
from email.message import EmailMessage
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None
import os
import requests
from ..extensions import db
from ..models import Client, Company, ClientCompanyLink, RelationStatus, Order, LogisticsStatus, Collection, CollectionPayment, PaymentMethod, ClientBranch, ClientDeliveryPlace, ClientBirthday, OrderAttachment, ClientDocument, CompanyDocument, ClientAlertState
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError

bp = Blueprint("main", __name__)


def _compute_alerts_for_all_clients(now_dt: datetime):
    alerts = []
    clients = Client.query.order_by(Client.apellido, Client.nombre).all()
    for c in clients:
        try:
            state_rows = ClientAlertState.query.filter_by(client_id=c.id).all()
        except OperationalError:
            # DB aún no migrada (por ejemplo SQLite sin columnas nuevas)
            state_rows = []
        state_map = {(s.order_id, s.kind): s for s in state_rows}

        for o in (c.orders or []):
            comp_name = o.company.nombre if o.company else "-"

            def _is_muted(kind: str) -> bool:
                st = state_map.get((o.id, kind))
                if not st:
                    return False
                if st.dismissed_at:
                    return True
                if st.snoozed_until and st.snoozed_until > now_dt:
                    return True
                return False

            # Mercadería
            lg = getattr(o, "logistics", None)
            due = None
            if lg and not lg.fecha_entrega_efectiva:
                due = lg.fecha_entrega_estimada
            if not due and o.demora_despacho_promedio_dias is not None:
                try:
                    due = o.created_at + timedelta(days=int(o.demora_despacho_promedio_dias or 0))
                except Exception:
                    due = None
            if due and now_dt > due and not _is_muted("MERCADERIA"):
                overdue_days = (now_dt - due).days
                alerts.append({
                    "client_id": c.id,
                    "client_name": f"{c.apellido} {c.nombre}",
                    "company_id": o.company_id,
                    "order_id": o.id,
                    "kind": "MERCADERIA",
                    "severity": "warning" if overdue_days <= 2 else "danger",
                    "company": comp_name,
                    "message": f"Mercadería atrasada ({comp_name}). Vencida hace {overdue_days} día(s).",
                })

            # Cobranzas
            col = getattr(o, "collection", None)
            if col and not col.fecha_cobro_efectiva:
                pay_due = col.fecha_pago_estimada
                if not pay_due and o.company and o.company.plazo_pago_promedio_dias is not None:
                    try:
                        plazo_days = None
                        if getattr(o, "plazo_pago_dias", None) is not None:
                            plazo_days = int(o.plazo_pago_dias or 0)
                        else:
                            plazo_days = int(o.company.plazo_pago_promedio_dias or 0)
                        pay_due = o.created_at + timedelta(days=plazo_days)
                    except Exception:
                        pay_due = None
                if pay_due and now_dt > pay_due and not _is_muted("COBRANZA"):
                    overdue_days = (now_dt - pay_due).days
                    alerts.append({
                        "client_id": c.id,
                        "client_name": f"{c.apellido} {c.nombre}",
                        "company_id": o.company_id,
                        "order_id": o.id,
                        "kind": "COBRANZA",
                        "severity": "warning" if overdue_days <= 2 else "danger",
                        "company": comp_name,
                        "message": f"Cobranza vencida ({comp_name}). Vencida hace {overdue_days} día(s).",
                    })
    return alerts


def _refresh_link_statuses_by_last_order(now_dt: datetime, client_ids=None):
    threshold = now_dt - timedelta(days=90)

    q_links = ClientCompanyLink.query
    if client_ids:
        q_links = q_links.filter(ClientCompanyLink.client_id.in_(client_ids))
    links = q_links.all()
    if not links:
        return

    pairs = {(l.client_id, l.company_id) for l in links}
    if not pairs:
        return

    from sqlalchemy import or_, and_
    conds = [and_(Order.client_id == cid, Order.company_id == coid) for (cid, coid) in pairs]
    last_map = {}
    if conds:
        rows = (
            db.session.query(Order.client_id, Order.company_id, func.max(Order.created_at))
            .filter(or_(*conds))
            .group_by(Order.client_id, Order.company_id)
            .all()
        )
        last_map = {(cid, coid): last_dt for (cid, coid, last_dt) in rows}

    changed = False
    for l in links:
        if l.status != RelationStatus.TRABAJA:
            continue
        last_dt = last_map.get((l.client_id, l.company_id))
        if (not last_dt) or (last_dt < threshold):
            l.status = RelationStatus.TRABAJABA
            changed = True
    if changed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


@bp.before_app_request
def auto_refresh_link_statuses_global():
    # Ejecutar la regla en toda la app, pero con throttling para no impactar rendimiento.
    # Se ejecuta como máximo cada 10 minutos por proceso.
    try:
        if request.endpoint and str(request.endpoint).startswith("static"):
            return
        last = current_app.config.get("_LAST_LINK_REFRESH_AT")
        now_dt = datetime.utcnow()
        if last and isinstance(last, datetime) and (now_dt - last) < timedelta(minutes=10):
            return
        current_app.config["_LAST_LINK_REFRESH_AT"] = now_dt
        _refresh_link_statuses_by_last_order(now_dt)
    except Exception:
        # Nunca romper navegación por errores en tarea automática
        try:
            db.session.rollback()
        except Exception:
            pass


@bp.before_app_request
def auto_patch_new_columns():
    # Evitar caídas por DB desactualizada (especialmente SQLite): agregar columnas nuevas si faltan.
    # Se intenta solo una vez por proceso.
    try:
        if request.endpoint and str(request.endpoint).startswith("static"):
            return
        if current_app.config.get("_DID_PATCH_NEW_COLS"):
            return
        current_app.config["_DID_PATCH_NEW_COLS"] = True
        dialect = db.session.bind.dialect.name if db.session.bind is not None else ""
        if dialect == "postgresql":
            stmts = [
                """
                CREATE TABLE IF NOT EXISTS client_document (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER NOT NULL,
                    category VARCHAR(32),
                    filename VARCHAR(255) NOT NULL,
                    filepath VARCHAR(500) NOT NULL,
                    data BYTEA,
                    mimetype VARCHAR(120),
                    size INTEGER,
                    uploaded_at TIMESTAMP
                )
                """ ,
                "ALTER TABLE client_company_link ADD COLUMN IF NOT EXISTS descuento NUMERIC(5,2)",
                "ALTER TABLE client_delivery_place ADD COLUMN IF NOT EXISTS provincia VARCHAR(80)",
                "ALTER TABLE client_delivery_place ADD COLUMN IF NOT EXISTS nota VARCHAR(255)",
                "ALTER TABLE client_birthday ADD COLUMN IF NOT EXISTS notas TEXT",
                "ALTER TABLE client ADD COLUMN IF NOT EXISTS transporte_contacto VARCHAR(255)",
                "ALTER TABLE client ADD COLUMN IF NOT EXISTS forma_pago_habitual VARCHAR(32)",
                "ALTER TABLE client_document ADD COLUMN IF NOT EXISTS category VARCHAR(32)",
                "ALTER TABLE company ADD COLUMN IF NOT EXISTS pedido_estandar_recomendado TEXT",
                "ALTER TABLE company ADD COLUMN IF NOT EXISTS plazo_usual VARCHAR(120)",
                "ALTER TABLE company ADD COLUMN IF NOT EXISTS forma_pago_default VARCHAR(32)",
                "ALTER TABLE \"order\" ADD COLUMN IF NOT EXISTS plazo_pago_dias INTEGER",
                """
                CREATE TABLE IF NOT EXISTS collection_payment (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    kind VARCHAR(16) NOT NULL,
                    method VARCHAR(32),
                    amount NUMERIC(12,2),
                    due_date TIMESTAMP,
                    attachment_url VARCHAR(500),
                    notes TEXT,
                    created_at TIMESTAMP
                )
                """,
            ]
        else:
            stmts = [
                """
                CREATE TABLE IF NOT EXISTS client_document (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    category VARCHAR(32),
                    filename VARCHAR(255) NOT NULL,
                    filepath VARCHAR(500) NOT NULL,
                    data BLOB,
                    mimetype VARCHAR(120),
                    size INTEGER,
                    uploaded_at DATETIME
                )
                """ ,
                "ALTER TABLE client_company_link ADD COLUMN descuento REAL",
                "ALTER TABLE client_delivery_place ADD COLUMN provincia VARCHAR(80)",
                "ALTER TABLE client_delivery_place ADD COLUMN nota VARCHAR(255)",
                "ALTER TABLE client_birthday ADD COLUMN notas TEXT",
                "ALTER TABLE client ADD COLUMN transporte_contacto VARCHAR(255)",
                "ALTER TABLE client ADD COLUMN forma_pago_habitual VARCHAR(32)",
                "ALTER TABLE client_document ADD COLUMN category VARCHAR(32)",
                "ALTER TABLE company ADD COLUMN pedido_estandar_recomendado TEXT",
                "ALTER TABLE company ADD COLUMN plazo_usual VARCHAR(120)",
                "ALTER TABLE company ADD COLUMN forma_pago_default VARCHAR(32)",
                "ALTER TABLE \"order\" ADD COLUMN plazo_pago_dias INTEGER",
                """
                CREATE TABLE IF NOT EXISTS collection_payment (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    kind VARCHAR(16) NOT NULL,
                    method VARCHAR(32),
                    amount REAL,
                    due_date DATETIME,
                    attachment_url VARCHAR(500),
                    notes TEXT,
                    created_at DATETIME
                )
                """,
            ]
        for sql in stmts:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except Exception:
                db.session.rollback()
                continue
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


@bp.app_context_processor
def inject_notification_count():
    try:
        now_dt = datetime.utcnow()
        active_alerts = _compute_alerts_for_all_clients(now_dt)
        return {"notif_active_count": len(active_alerts)}
    except Exception:
        return {"notif_active_count": 0}


@bp.get("/")
def index():
    # KPIs y métricas para dashboard
    today = date.today()
    month_start = today.replace(day=1)
    # Pedidos del mes
    pedidos_mes = Order.query.filter(Order.created_at >= datetime.combine(month_start, datetime.min.time())).count()
    # Ventas del mes (suma de precio_final)
    ventas_mes_val = db.session.query(func.coalesce(func.sum(Order.precio_final), 0)).filter(Order.created_at >= datetime.combine(month_start, datetime.min.time())).scalar() or 0
    # Cobranzas pendientes (cantidad y monto)
    cobranzas_pend_count = Collection.query.filter(Collection.fecha_cobro_efectiva.is_(None)).count()
    cobranzas_pend_monto = db.session.query(func.coalesce(func.sum(Collection.monto), 0)).filter(Collection.fecha_cobro_efectiva.is_(None)).scalar() or 0
    # Status logística: en camino y atrasados
    now_dt = datetime.utcnow()
    en_camino = (
        db.session.query(func.count(LogisticsStatus.id))
        .filter(LogisticsStatus.fecha_entrega_efectiva.is_(None))
        .filter((LogisticsStatus.fecha_entrega_estimada.is_(None)) | (LogisticsStatus.fecha_entrega_estimada >= now_dt))
        .scalar()
    ) or 0
    atrasados = (
        db.session.query(func.count(LogisticsStatus.id))
        .filter(LogisticsStatus.fecha_entrega_efectiva.is_(None))
        .filter(LogisticsStatus.fecha_entrega_estimada < now_dt)
        .scalar()
    ) or 0

    # Series últimos 30 días
    last_30 = [date.fromordinal(today.toordinal() - i) for i in range(29, -1, -1)]
    labels = [d.strftime("%d/%m") for d in last_30]
    iso_keys = [d.isoformat() for d in last_30]
    sales_by_day = {k: 0.0 for k in iso_keys}
    # Ventas por día: usar Order.created_at y precio_final
    orders_last_30 = Order.query.filter(Order.created_at >= datetime.combine(last_30[0], datetime.min.time())).all()
    for o in orders_last_30:
        key = o.created_at.date().isoformat()
        try:
            sales_by_day[key] += float(o.precio_final or 0)
        except Exception:
            pass
    daily_sales = [round(sales_by_day[k], 2) for k in iso_keys]

    # Top 5 empresas por ventas últimos 30 días
    from collections import defaultdict
    ventas_por_empresa = defaultdict(float)
    for o in orders_last_30:
        if o.company and o.precio_final:
            try:
                ventas_por_empresa[o.company.nombre] += float(o.precio_final or 0)
            except Exception:
                continue
    top_emp = sorted(ventas_por_empresa.items(), key=lambda x: x[1], reverse=True)[:5]
    top_emp_labels = [n for n, _ in top_emp]
    top_emp_values = [round(v, 2) for _, v in top_emp]

    # Urgentes/atrasadas/próximos 7 días (entregas y cobranzas)
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())

    # Cobranzas
    cobr_vencidas_q = (
        Collection.query
        .filter(Collection.fecha_cobro_efectiva.is_(None))
        .filter(Collection.fecha_pago_estimada.isnot(None))
        .filter(Collection.fecha_pago_estimada < now_dt)
    )
    cobr_hoy_q = (
        Collection.query
        .filter(Collection.fecha_cobro_efectiva.is_(None))
        .filter(Collection.fecha_pago_estimada >= today_start, Collection.fecha_pago_estimada <= today_end)
    )
    cobr_next7_q = (
        Collection.query
        .filter(Collection.fecha_cobro_efectiva.is_(None))
        .filter(Collection.fecha_pago_estimada > now_dt, Collection.fecha_pago_estimada <= now_dt + timedelta(days=7))
    )

    # Entregas
    ent_vencidas_q = (
        LogisticsStatus.query
        .filter(LogisticsStatus.fecha_entrega_efectiva.is_(None))
        .filter(LogisticsStatus.fecha_entrega_estimada.isnot(None))
        .filter(LogisticsStatus.fecha_entrega_estimada < now_dt)
    )
    ent_hoy_q = (
        LogisticsStatus.query
        .filter(LogisticsStatus.fecha_entrega_efectiva.is_(None))
        .filter(LogisticsStatus.fecha_entrega_estimada >= today_start, LogisticsStatus.fecha_entrega_estimada <= today_end)
    )
    ent_next7_q = (
        LogisticsStatus.query
        .filter(LogisticsStatus.fecha_entrega_efectiva.is_(None))
        .filter(LogisticsStatus.fecha_entrega_estimada > now_dt, LogisticsStatus.fecha_entrega_estimada <= now_dt + timedelta(days=7))
    )

    urgentes_hoy = ent_hoy_q.count() + cobr_hoy_q.count()
    atrasadas_cnt = ent_vencidas_q.count() + cobr_vencidas_q.count()
    proximas7_cnt = ent_next7_q.count() + cobr_next7_q.count()

    # Construir lista resumida de urgentes (top 10), sin duplicados y con fecha en AR
    def _row_from_ent(e: LogisticsStatus, categoria: str):
        o = e.order
        # fecha en AR (solo día)
        if ZoneInfo and e.fecha_entrega_estimada:
            dt = e.fecha_entrega_estimada.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
            ftxt = dt.strftime("%d/%m/%Y")
        else:
            ftxt = e.fecha_entrega_estimada.strftime("%d/%m/%Y") if e.fecha_entrega_estimada else ""
        return {
            "tipo": "Entrega",
            "cliente": f"{o.client.apellido} {o.client.nombre}" if o and o.client else "-",
            "empresa": o.company.nombre if o and o.company else "-",
            "fecha": ftxt,
            "monto": float(e.precio or (o.precio_final or 0) if o else 0),
            "_key": ("E", e.order_id),
            "categoria": categoria,
        }

    def _row_from_cobr(c: Collection, categoria: str):
        o = c.order
        if ZoneInfo and c.fecha_pago_estimada:
            dt = c.fecha_pago_estimada.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
            ftxt = dt.strftime("%d/%m/%Y")
        else:
            ftxt = c.fecha_pago_estimada.strftime("%d/%m/%Y") if c.fecha_pago_estimada else ""
        return {
            "tipo": "Cobranza",
            "cliente": f"{o.client.apellido} {o.client.nombre}" if o and o.client else "-",
            "empresa": o.company.nombre if o and o.company else "-",
            "fecha": ftxt,
            "monto": float(c.monto or 0),
            "_key": ("C", c.order_id),
            "categoria": categoria,
        }

    # Agrupar por pedido (tipo+order_id) y acumular categorías
    agg = {}
    def add_row(row):
        key = row["_key"]
        if key not in agg:
            # copiar base y arrancar categorias
            base = {k: v for k, v in row.items() if k not in ("_key", "categoria")}
            base["categorias"] = [row.get("categoria")] if row.get("categoria") else []
            agg[key] = base
        else:
            cat = row.get("categoria")
            if cat and cat not in agg[key]["categorias"]:
                agg[key]["categorias"].append(cat)

    for e in ent_vencidas_q.limit(5).all():
        add_row(_row_from_ent(e, "Atrasado total"))
    for c in cobr_vencidas_q.limit(5).all():
        add_row(_row_from_cobr(c, "Atrasado total"))
    for e in ent_hoy_q.limit(5).all():
        add_row(_row_from_ent(e, "Urg de hoy"))
    for c in cobr_hoy_q.limit(5).all():
        add_row(_row_from_cobr(c, "Urg de hoy"))
    for e in ent_next7_q.limit(5).all():
        add_row(_row_from_ent(e, "Prox 7 días"))
    for c in cobr_next7_q.limit(5).all():
        add_row(_row_from_cobr(c, "Prox 7 días"))

    urg_rows = list(agg.values())
    urgentes = urg_rows[:10]

    kpis = {
        "pedidos_mes": int(pedidos_mes),
        "ventas_mes_val": float(ventas_mes_val or 0),
        "cobranzas_pend_count": int(cobranzas_pend_count),
        "cobranzas_pend_monto": float(cobranzas_pend_monto or 0),
        "en_camino": int(en_camino),
        "atrasados": int(atrasados),
        # nuevos
        "urgentes_hoy": int(urgentes_hoy),
        "atrasadas_total": int(atrasadas_cnt),
        "proximas7": int(proximas7_cnt),
    }
    charts = {
        "labels": labels,
        "daily_sales": daily_sales,
        "top_emp_labels": top_emp_labels,
        "top_emp_values": top_emp_values,
    }
    extras = {"urgentes": urgentes}
    return render_template("index.html", active="dashboard", kpis=kpis, charts=charts, extras=extras)


@bp.get("/clientes")
def clientes():
    items = Client.query.order_by(Client.apellido, Client.nombre).all()
    companies = Company.query.order_by(Company.nombre).all()

    # Regla: si pasaron 3 meses sin pedidos, TRABAJA -> TRABAJABA
    now_dt = datetime.utcnow()
    _refresh_link_statuses_by_last_order(now_dt, [c.id for c in items])

    alerts_by_client = {}
    total_active_alerts = 0

    for c in items:
        # Estados persistidos para filtrar alertas (visto / postergado)
        try:
            state_rows = ClientAlertState.query.filter_by(client_id=c.id).all()
        except OperationalError:
            state_rows = []
        state_map = {(s.order_id, s.kind): s for s in state_rows}

        client_alerts = []

        for o in (c.orders or []):
            comp_name = o.company.nombre if o.company else "-"

            def _is_muted(kind: str) -> bool:
                st = state_map.get((o.id, kind))
                if not st:
                    return False
                if st.dismissed_at:
                    return True
                if st.snoozed_until and st.snoozed_until > now_dt:
                    return True
                return False

            # Mercadería / logística (atrasos)
            lg = getattr(o, "logistics", None)
            if lg and not lg.fecha_entrega_efectiva:
                due = lg.fecha_entrega_estimada
                if not due and o.demora_despacho_promedio_dias is not None:
                    try:
                        due = o.created_at + timedelta(days=int(o.demora_despacho_promedio_dias or 0))
                    except Exception:
                        due = None
                if due and now_dt > due:
                    overdue_days = (now_dt - due).days
                    if not _is_muted("MERCADERIA"):
                        client_alerts.append({
                        "kind": "MERCADERIA",
                        "severity": "warning" if overdue_days <= 2 else "danger",
                        "order_id": o.id,
                        "company": comp_name,
                        "message": f"Mercadería atrasada ({comp_name}). Vencida hace {overdue_days} día(s).",
                        })
            elif not lg:
                # Si no hay registro de logística, usar snapshot de demora para recordatorio
                if o.demora_despacho_promedio_dias is not None:
                    try:
                        due = o.created_at + timedelta(days=int(o.demora_despacho_promedio_dias or 0))
                    except Exception:
                        due = None
                    if due and now_dt > due:
                        overdue_days = (now_dt - due).days
                        if not _is_muted("MERCADERIA"):
                            client_alerts.append({
                            "kind": "MERCADERIA",
                            "severity": "warning" if overdue_days <= 2 else "danger",
                            "order_id": o.id,
                            "company": comp_name,
                            "message": f"Revisar despacho ({comp_name}). Pasaron {overdue_days} día(s) sobre la demora estimada.",
                            })

            # Pagos / cobranzas (atrasos)
            col = getattr(o, "collection", None)
            if col and not col.fecha_cobro_efectiva:
                pay_due = col.fecha_pago_estimada
                if not pay_due and o.company and o.company.plazo_pago_promedio_dias is not None:
                    try:
                        pay_due = o.created_at + timedelta(days=int(o.company.plazo_pago_promedio_dias or 0))
                    except Exception:
                        pay_due = None
                if pay_due and now_dt > pay_due:
                    overdue_days = (now_dt - pay_due).days
                    if not _is_muted("COBRANZA"):
                        client_alerts.append({
                        "kind": "COBRANZA",
                        "severity": "warning" if overdue_days <= 2 else "danger",
                        "order_id": o.id,
                        "company": comp_name,
                        "message": f"Cobranza vencida ({comp_name}). Vencida hace {overdue_days} día(s).",
                        })

        alerts_by_client[c.id] = client_alerts
        total_active_alerts += len(client_alerts)

    return render_template(
        "clientes.html",
        active="clientes",
        items=items,
        companies=companies,
        alerts_by_client=alerts_by_client,
        total_active_alerts=total_active_alerts,
    )


@bp.post("/clientes/alertas/visto")
def clientes_alertas_visto():
    client_id = request.form.get("client_id", type=int)
    order_id = request.form.get("order_id", type=int)
    kind = (request.form.get("kind") or "").strip().upper()
    if not client_id or not order_id or not kind:
        return jsonify({"ok": False, "error": "missing_params"}), 400

    try:
        st = ClientAlertState.query.filter_by(client_id=client_id, order_id=order_id, kind=kind).first()
    except OperationalError:
        return jsonify({"ok": False, "error": "db_schema_outdated", "hint": "Ejecutar /admin/patch_client_columns"}), 500
    if not st:
        st = ClientAlertState(client_id=client_id, order_id=order_id, kind=kind)
        db.session.add(st)
    # Snapshot para historial
    st.message = (request.form.get("message") or st.message or "").strip() or st.message
    st.severity = (request.form.get("severity") or st.severity or "").strip() or st.severity
    st.company = (request.form.get("company") or st.company or "").strip() or st.company
    if not st.first_seen_at:
        st.first_seen_at = datetime.utcnow()
    st.dismissed_at = datetime.utcnow()
    st.snoozed_until = None
    try:
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_schema_outdated", "hint": "Ejecutar /admin/patch_client_columns"}), 500
    return jsonify({"ok": True})


@bp.post("/clientes/alertas/snooze")
def clientes_alertas_snooze():
    client_id = request.form.get("client_id", type=int)
    order_id = request.form.get("order_id", type=int)
    kind = (request.form.get("kind") or "").strip().upper()
    days = request.form.get("days", type=int)
    if not client_id or not order_id or not kind or not days:
        return jsonify({"ok": False, "error": "missing_params"}), 400
    if days not in (1, 7, 30):
        return jsonify({"ok": False, "error": "invalid_days"}), 400

    try:
        st = ClientAlertState.query.filter_by(client_id=client_id, order_id=order_id, kind=kind).first()
    except OperationalError:
        return jsonify({"ok": False, "error": "db_schema_outdated", "hint": "Ejecutar /admin/patch_client_columns"}), 500
    if not st:
        st = ClientAlertState(client_id=client_id, order_id=order_id, kind=kind)
        db.session.add(st)

    st.message = (request.form.get("message") or st.message or "").strip() or st.message
    st.severity = (request.form.get("severity") or st.severity or "").strip() or st.severity
    st.company = (request.form.get("company") or st.company or "").strip() or st.company
    if not st.first_seen_at:
        st.first_seen_at = datetime.utcnow()
    st.dismissed_at = None
    st.snoozed_until = datetime.utcnow() + timedelta(days=int(days))

    try:
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_schema_outdated", "hint": "Ejecutar /admin/patch_client_columns"}), 500
    return jsonify({"ok": True, "snoozed_until": st.snoozed_until.isoformat() if st.snoozed_until else None})


@bp.get("/notificaciones")
def notificaciones():
    now_dt = datetime.utcnow()
    active_alerts = _compute_alerts_for_all_clients(now_dt)
    try:
        viewed_rows = (
            ClientAlertState.query
            .filter(ClientAlertState.dismissed_at.isnot(None))
            .order_by(ClientAlertState.dismissed_at.desc())
            .limit(200)
            .all()
        )
    except OperationalError:
        viewed_rows = []
    # Map para mostrar nombre de cliente en historial
    client_ids = sorted({r.client_id for r in viewed_rows if r and r.client_id})
    clients = Client.query.filter(Client.id.in_(client_ids)).all() if client_ids else []
    client_name_map = {c.id: f"{c.apellido} {c.nombre}" for c in clients}
    viewed = [
        {
            "client_id": r.client_id,
            "client_name": client_name_map.get(r.client_id, "-"),
            "order_id": r.order_id,
            "kind": r.kind,
            "company": r.company,
            "severity": r.severity,
            "message": r.message,
            "dismissed_at": r.dismissed_at,
        }
        for r in viewed_rows
    ]
    return render_template(
        "notificaciones.html",
        active="notificaciones",
        active_alerts=active_alerts,
        viewed_alerts=viewed,
    )


# Calendario general (entregas y cobranzas)
@bp.get("/calendario")
def calendario():
    today = date.today().isoformat()
    return render_template("calendar.html", active="calendario", today=today)


@bp.get("/comisiones")
def comisiones():
    return render_template("comisiones.html", active="comisiones")


@bp.get("/api/calendario/events")
def api_calendario_events():
    # Rango opcional
    start_raw = request.args.get("start")
    end_raw = request.args.get("end")
    try:
        start = datetime.fromisoformat(start_raw) if start_raw else None
    except Exception:
        start = None
    try:
        end = datetime.fromisoformat(end_raw) if end_raw else None
    except Exception:
        end = None

    events = []
    # Entregas estimadas (pendientes)
    q_ent = LogisticsStatus.query.filter(LogisticsStatus.fecha_entrega_efectiva.is_(None))
    if start:
        q_ent = q_ent.filter(LogisticsStatus.fecha_entrega_estimada >= start)
    if end:
        q_ent = q_ent.filter(LogisticsStatus.fecha_entrega_estimada <= end)
    for lg in q_ent.filter(LogisticsStatus.fecha_entrega_estimada.isnot(None)).all():
        o = lg.order
        # Convertir a fecha local si es posible
        dt = lg.fecha_entrega_estimada
        if ZoneInfo:
            try:
                dt = dt.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
            except Exception:
                pass
        title = f"Entrega: {o.client.apellido} {o.client.nombre} - {o.company.nombre}" if o and o.client and o.company else "Entrega"
        events.append({
            "id": f"E-{o.id}",
            "title": title,
            "start": dt.date().isoformat(),
            "allDay": True,
            "color": "#0ea5a3"
        })

    # Cobranzas estimadas (pendientes)
    q_cob = Collection.query.filter(Collection.fecha_cobro_efectiva.is_(None))
    if start:
        q_cob = q_cob.filter(Collection.fecha_pago_estimada >= start)
    if end:
        q_cob = q_cob.filter(Collection.fecha_pago_estimada <= end)
    for c in q_cob.filter(Collection.fecha_pago_estimada.isnot(None)).all():
        o = c.order
        dt = c.fecha_pago_estimada
        if ZoneInfo:
            try:
                dt = dt.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
            except Exception:
                pass
        title = f"Cobranza: {o.client.apellido} {o.client.nombre} - {o.company.nombre}" if o and o.client and o.company else "Cobranza"
        events.append({
            "id": f"C-{o.id}",
            "title": title,
            "start": dt.date().isoformat(),
            "allDay": True,
            "color": "#f59e0b"
        })

    # Cumpleaños de contactos de clientes
    if start or end:
        # Determinar rango de años a considerar
        year_start = (start.date().year if start else date.today().year)
        year_end = (end.date().year if end else year_start)
        bdays = ClientBirthday.query.filter(ClientBirthday.fecha.isnot(None)).all()
        for b in bdays:
            if not b.fecha:
                continue
            # Para cada año en el rango, crear un evento en ese año
            for y in range(year_start, year_end + 1):
                try:
                    d = date(y, b.fecha.month, b.fecha.day)
                except ValueError:
                    continue
                # Filtrar por rango start/end
                if start and d < start.date():
                    continue
                if end and d > end.date():
                    continue
                client = b.client
                if client:
                    # Elegir una empresa representativa: priorizar vínculos en estado TRABAJA
                    empresa = None
                    try:
                        for l in client.links:
                            if l.company:
                                empresa = l.company
                                if getattr(l, "status", None) == RelationStatus.TRABAJA:
                                    break
                    except Exception:
                        empresa = None
                    emp_name = empresa.nombre if empresa and getattr(empresa, "nombre", None) else "-"
                    if b.puesto:
                        title = f"Cumpleaños: {emp_name} - {b.nombre} ({b.puesto})"
                    else:
                        title = f"Cumpleaños: {emp_name} - {b.nombre}"
                else:
                    title = f"Cumpleaños: {b.nombre}"
                events.append({
                    "id": f"B-{b.id}-{y}",
                    "title": title,
                    "start": d.isoformat(),
                    "allDay": True,
                    "color": "#ec4899"  # rosa para distinguir cumpleaños
                })

    return jsonify(events)


@bp.get("/clientes/nuevo")
def clientes_new():
    items = Client.query.order_by(Client.apellido, Client.nombre).all()
    companies = Company.query.order_by(Company.nombre).all()
    branches = [r[0] for r in db.session.query(Client.sucursal).filter(Client.sucursal.isnot(None)).distinct().order_by(Client.sucursal).all()]
    return render_template("clientes_form.html", active="clientes", companies=companies, branches=branches)


@bp.post("/clientes/nuevo")
def clientes_create():
    apellido = request.form.get("apellido", "").strip()
    nombre = request.form.get("nombre", "").strip()
    cuit = (request.form.get("cuit", "") or "").strip() or None
    # sucursales múltiples
    branch_list = [b.strip() for b in request.form.getlist("branch_list") if (b or "").strip()]
    telefono = request.form.get("telefono", "").strip()
    provincia = (request.form.get("provincia") or "").strip() or None
    forma_pago_habitual = (request.form.get("forma_pago_habitual") or "").strip().upper() or None
    # Transporte (sección colapsable)
    transporte_nombre = (request.form.get("transporte_nombre") or "").strip() or None
    transporte_contacto = (request.form.get("transporte_contacto") or "").strip() or None
    # Mails múltiples
    mail_single = (request.form.get("mail", "") or "").strip()
    mail = (mail_single or None)
    relacion = (request.form.get("relacion") or "").strip() or None
    fecha_inc = request.form.get("fecha_incorporacion") or None
    company_id = request.form.get("company_id", type=int)
    # Direcciones múltiples con provincia, nota, horarios
    delivery_names = [d.strip() for d in request.form.getlist("delivery_name_list")]
    delivery_provincias = [d.strip() for d in request.form.getlist("delivery_provincia_list")]
    delivery_notas = [d.strip() for d in request.form.getlist("delivery_nota_list")]
    delivery_schedules = [d.strip() for d in request.form.getlist("delivery_schedule_list")]
    delivery_contacts = [d.strip() for d in request.form.getlist("delivery_contact_list")]
    delivery_phones = [d.strip() for d in request.form.getlist("delivery_phone_list")]
    # Personas (cumpleaños) con notas
    b_names = [v.strip() for v in request.form.getlist("birthday_name_list")]
    b_roles = [v.strip() for v in request.form.getlist("birthday_role_list")]
    b_dates = [v.strip() for v in request.form.getlist("birthday_date_list")]
    b_notas = [v.strip() for v in request.form.getlist("birthday_notas_list")]
    # Guardar compat 'sucursal' como la primera si viene lista
    compat_sucursal = (branch_list[0] if branch_list else None)
    client = Client(apellido=apellido or "-", nombre=nombre or "-", sucursal=compat_sucursal,
                    cuit=cuit,
                    transporte_recomendado=transporte_nombre, transporte_contacto=transporte_contacto,
                    provincia=provincia,
                    forma_pago_habitual=forma_pago_habitual,
                    telefono=telefono or None, mail=mail or None,
                    fecha_incorporacion=date.fromisoformat(fecha_inc) if fecha_inc else None)
    db.session.add(client)
    db.session.commit()

    # Sincronizar empresas confirmadas si vinieron company_ids desde el formulario
    company_ids = request.form.getlist("company_ids", type=int)
    if company_ids:
        try:
            keep_ids = set(company_ids)
            for coid in keep_ids:
                comp = Company.query.get(coid)
                if not comp:
                    continue
                link = ClientCompanyLink.query.filter_by(client_id=client.id, company_id=coid).first()
                if not link:
                    db.session.add(ClientCompanyLink(client_id=client.id, company_id=coid, status=RelationStatus.TRABAJA, comprobante_tipo="FACTURA"))
            db.session.commit()
        except Exception:
            db.session.rollback()
    # Crear sucursales
    if branch_list:
        for nm in branch_list:
            try:
                db.session.add(ClientBranch(client_id=client.id, nombre=nm))
            except Exception:
                pass
        db.session.commit()
    # Crear personas (cumpleaños) con notas
    if b_names or b_roles or b_dates or b_notas:
        try:
            max_len_b = max(len(b_names), len(b_roles), len(b_dates), len(b_notas))
            for idx in range(max_len_b):
                nm = (b_names[idx] if idx < len(b_names) else "").strip()
                rl = (b_roles[idx] if idx < len(b_roles) else "").strip()
                dt_raw = (b_dates[idx] if idx < len(b_dates) else "").strip()
                nt = (b_notas[idx] if idx < len(b_notas) else "").strip()
                if not (nm or rl or dt_raw or nt):
                    continue
                fecha = None
                if dt_raw:
                    try:
                        fecha = date.fromisoformat(dt_raw) if "-" in dt_raw else None
                    except Exception:
                        fecha = None
                db.session.add(ClientBirthday(client_id=client.id, nombre=nm or "-", puesto=rl or None, fecha=fecha, notas=nt or None))
            db.session.commit()
        except Exception:
            db.session.rollback()
    # Crear direcciones con provincia, nota, horarios
    if delivery_names or delivery_provincias or delivery_notas or delivery_schedules or delivery_contacts or delivery_phones:
        try:
            max_len = max(len(delivery_names), len(delivery_provincias), len(delivery_notas), len(delivery_schedules), len(delivery_contacts), len(delivery_phones))
            for idx in range(max_len):
                nm = (delivery_names[idx] if idx < len(delivery_names) else "").strip()
                pv = (delivery_provincias[idx] if idx < len(delivery_provincias) else "").strip()
                nt = (delivery_notas[idx] if idx < len(delivery_notas) else "").strip()
                hs = (delivery_schedules[idx] if idx < len(delivery_schedules) else "").strip()
                ct = (delivery_contacts[idx] if idx < len(delivery_contacts) else "").strip()
                ph = (delivery_phones[idx] if idx < len(delivery_phones) else "").strip()
                if not (nm or pv or nt or hs or ct or ph):
                    continue
                db.session.add(ClientDeliveryPlace(client_id=client.id, nombre=nm or "-", provincia=pv or None, nota=nt or None, horario=hs or None, contacto=ct or None, telefono=ph or None))
            db.session.commit()
        except Exception:
            db.session.rollback()
    # Si se indicó relación y empresa, crear/actualizar vínculo; si no hay empresa, aplicar a vínculos existentes
    if relacion and company_id:
        try:
            st = RelationStatus(relacion)
            comp = Company.query.get(company_id)
            if comp:
                link = ClientCompanyLink.query.filter_by(client_id=client.id, company_id=comp.id).first()
                if not link:
                    link = ClientCompanyLink(client_id=client.id, company_id=comp.id, status=st, comprobante_tipo="FACTURA")
                    db.session.add(link)
                else:
                    link.status = st
                db.session.commit()
        except Exception:
            pass
    elif relacion:
        try:
            st = RelationStatus(relacion)
            for l in client.links:
                l.status = st
            db.session.commit()
        except Exception:
            pass
    return redirect(url_for("main.clientes"))


@bp.get("/admin/patch_client_columns")
def admin_patch_client_columns():
    # Ajustar sintaxis según motor (SQLite no soporta ADD COLUMN IF NOT EXISTS en todas las versiones)
    dialect = db.session.bind.dialect.name if db.session.bind is not None else ""
    if dialect == "postgresql":
        alter_link = "ALTER TABLE client_company_link ADD COLUMN IF NOT EXISTS comprobante_tipo VARCHAR(20) DEFAULT 'FACTURA'"
        alter_link_desc = "ALTER TABLE client_company_link ADD COLUMN IF NOT EXISTS descuento NUMERIC(5,2)"
        alter_client_cols = [
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS cuit VARCHAR(32)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS direccion_principal VARCHAR(255)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS transporte_recomendado VARCHAR(120)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS delivery_schedule VARCHAR(255)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS delivery_contact VARCHAR(255)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS delivery_phone VARCHAR(64)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS provincia VARCHAR(80)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS fecha_incorporacion DATE",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS telefono VARCHAR(50)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS mail VARCHAR(255)",
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS forma_pago_habitual VARCHAR(32)",
        ]
        alter_company_cols = [
            "ALTER TABLE company ADD COLUMN IF NOT EXISTS cuit VARCHAR(32)",
            "ALTER TABLE company ADD COLUMN IF NOT EXISTS notas TEXT",
            "ALTER TABLE company ADD COLUMN IF NOT EXISTS cuenta_bancaria_notas TEXT",
        ]
        create_company_document = """
        CREATE TABLE IF NOT EXISTS company_document (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            category VARCHAR(32) NOT NULL,
            filename VARCHAR(255) NOT NULL,
            filepath VARCHAR(500) NOT NULL,
            data BYTEA,
            mimetype VARCHAR(120),
            size INTEGER,
            uploaded_at TIMESTAMP
        )
        """

        create_client_document = """
        CREATE TABLE IF NOT EXISTS client_document (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL,
            category VARCHAR(32),
            filename VARCHAR(255) NOT NULL,
            filepath VARCHAR(500) NOT NULL,
            data BYTEA,
            mimetype VARCHAR(120),
            size INTEGER,
            uploaded_at TIMESTAMP
        )
        """

        alter_client_document_cols = [
            "ALTER TABLE client_document ADD COLUMN IF NOT EXISTS category VARCHAR(32)",
        ]
        alter_delivery_place_cols = [
            "ALTER TABLE client_delivery_place ADD COLUMN IF NOT EXISTS horario VARCHAR(255)",
            "ALTER TABLE client_delivery_place ADD COLUMN IF NOT EXISTS contacto VARCHAR(255)",
            "ALTER TABLE client_delivery_place ADD COLUMN IF NOT EXISTS telefono VARCHAR(64)",
            "ALTER TABLE client_delivery_place ADD COLUMN IF NOT EXISTS provincia VARCHAR(80)",
            "ALTER TABLE client_delivery_place ADD COLUMN IF NOT EXISTS nota VARCHAR(255)",
        ]
        alter_birthday_cols = [
            "ALTER TABLE client_birthday ADD COLUMN IF NOT EXISTS notas TEXT",
        ]
        alter_client_transport_cols = [
            "ALTER TABLE client ADD COLUMN IF NOT EXISTS transporte_contacto VARCHAR(255)",
        ]
        create_client_alert_state = """
        CREATE TABLE IF NOT EXISTS client_alert_state (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL,
            order_id INTEGER NOT NULL,
            kind VARCHAR(32) NOT NULL,
            dismissed_at TIMESTAMP,
            snoozed_until TIMESTAMP,
            message TEXT,
            severity VARCHAR(16),
            company VARCHAR(200),
            first_seen_at TIMESTAMP,
            updated_at TIMESTAMP,
            CONSTRAINT uq_client_alert_state UNIQUE (client_id, order_id, kind)
        )
        """
        alter_client_alert_cols = [
            "ALTER TABLE client_alert_state ADD COLUMN IF NOT EXISTS message TEXT",
            "ALTER TABLE client_alert_state ADD COLUMN IF NOT EXISTS severity VARCHAR(16)",
            "ALTER TABLE client_alert_state ADD COLUMN IF NOT EXISTS company VARCHAR(200)",
            "ALTER TABLE client_alert_state ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP",
            "ALTER TABLE client_alert_state ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        ]
    else:
        # Sintaxis compatible con SQLite: si ya existe fallará pero lo ignoramos con try/except
        alter_link = "ALTER TABLE client_company_link ADD COLUMN comprobante_tipo VARCHAR(20) DEFAULT 'FACTURA'"
        alter_link_desc = "ALTER TABLE client_company_link ADD COLUMN descuento REAL"
        alter_client_cols = [
            "ALTER TABLE client ADD COLUMN cuit VARCHAR(32)",
            "ALTER TABLE client ADD COLUMN direccion_principal VARCHAR(255)",
            "ALTER TABLE client ADD COLUMN transporte_recomendado VARCHAR(120)",
            "ALTER TABLE client ADD COLUMN delivery_schedule VARCHAR(255)",
            "ALTER TABLE client ADD COLUMN delivery_contact VARCHAR(255)",
            "ALTER TABLE client ADD COLUMN delivery_phone VARCHAR(64)",
            "ALTER TABLE client ADD COLUMN provincia VARCHAR(80)",
            "ALTER TABLE client ADD COLUMN fecha_incorporacion DATE",
            "ALTER TABLE client ADD COLUMN telefono VARCHAR(50)",
            "ALTER TABLE client ADD COLUMN mail VARCHAR(255)",
            "ALTER TABLE client ADD COLUMN forma_pago_habitual VARCHAR(32)",
        ]
        alter_company_cols = [
            "ALTER TABLE company ADD COLUMN cuit VARCHAR(32)",
            "ALTER TABLE company ADD COLUMN notas TEXT",
            "ALTER TABLE company ADD COLUMN cuenta_bancaria_notas TEXT",
        ]
        create_company_document = """
        CREATE TABLE IF NOT EXISTS company_document (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            category VARCHAR(32) NOT NULL,
            filename VARCHAR(255) NOT NULL,
            filepath VARCHAR(500) NOT NULL,
            data BLOB,
            mimetype VARCHAR(120),
            size INTEGER,
            uploaded_at DATETIME
        )
        """

        create_client_document = """
        CREATE TABLE IF NOT EXISTS client_document (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            category VARCHAR(32),
            filename VARCHAR(255) NOT NULL,
            filepath VARCHAR(500) NOT NULL,
            data BLOB,
            mimetype VARCHAR(120),
            size INTEGER,
            uploaded_at DATETIME
        )
        """

        alter_client_document_cols = [
            "ALTER TABLE client_document ADD COLUMN category VARCHAR(32)",
        ]

        alter_delivery_place_cols = [
            "ALTER TABLE client_delivery_place ADD COLUMN horario VARCHAR(255)",
            "ALTER TABLE client_delivery_place ADD COLUMN contacto VARCHAR(255)",
            "ALTER TABLE client_delivery_place ADD COLUMN telefono VARCHAR(64)",
            "ALTER TABLE client_delivery_place ADD COLUMN provincia VARCHAR(80)",
            "ALTER TABLE client_delivery_place ADD COLUMN nota VARCHAR(255)",
        ]
        alter_birthday_cols = [
            "ALTER TABLE client_birthday ADD COLUMN notas TEXT",
        ]
        alter_client_transport_cols = [
            "ALTER TABLE client ADD COLUMN transporte_contacto VARCHAR(255)",
        ]

        create_client_alert_state = """
        CREATE TABLE IF NOT EXISTS client_alert_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            order_id INTEGER NOT NULL,
            kind VARCHAR(32) NOT NULL,
            dismissed_at DATETIME,
            snoozed_until DATETIME,
            message TEXT,
            severity VARCHAR(16),
            company VARCHAR(200),
            first_seen_at DATETIME,
            updated_at DATETIME,
            CONSTRAINT uq_client_alert_state UNIQUE (client_id, order_id, kind)
        )
        """

        alter_client_alert_cols = [
            "ALTER TABLE client_alert_state ADD COLUMN message TEXT",
            "ALTER TABLE client_alert_state ADD COLUMN severity VARCHAR(16)",
            "ALTER TABLE client_alert_state ADD COLUMN company VARCHAR(200)",
            "ALTER TABLE client_alert_state ADD COLUMN first_seen_at DATETIME",
            "ALTER TABLE client_alert_state ADD COLUMN updated_at DATETIME",
        ]

    stmts = [
        # Tablas relacionadas nuevas (crear si no existen)
        """
        CREATE TABLE IF NOT EXISTS client_delivery_place (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL,
            nombre VARCHAR(255) NOT NULL,
            CONSTRAINT uq_client_delivery_name UNIQUE (client_id, nombre)
        )
        """,
        # Nueva columna en vínculos empresa-cliente: tipo de comprobante (FACTURA/REMITO)
        alter_link,
        # Nueva columna en vínculos empresa-cliente: descuento (%)
        alter_link_desc,
        """
        CREATE TABLE IF NOT EXISTS client_birthday (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL,
            nombre VARCHAR(255) NOT NULL,
            puesto VARCHAR(255),
            fecha DATE,
            CONSTRAINT uq_client_birthday UNIQUE (client_id, nombre, puesto)
        )
        """,
        # Tabla de documentos de cliente (varía según motor)
        create_client_document,
        # Tabla de documentos de empresa (varía según motor)
        create_company_document,
        # Alertas persistidas (centro de notificaciones)
        create_client_alert_state,
        # Columnas nuevas para historial (si la tabla ya existía)
        *alter_client_alert_cols,
        # Columnas nuevas en client_delivery_place, client y company
        *alter_delivery_place_cols,
        *alter_client_cols,
        *alter_company_cols,
        # Columnas nuevas para personas (cumpleaños) y transporte
        *alter_birthday_cols,
        *alter_client_transport_cols,
        # Columnas nuevas en client_document
        *alter_client_document_cols,
    ]
    applied = []
    errors = []
    for sql in stmts:
        try:
            db.session.execute(text(sql))
            db.session.commit()
            applied.append(sql)
        except Exception as e:
            db.session.rollback()
            # Guardamos el error para diagnóstico (no rompe el resto)
            try:
                errors.append({"sql": sql, "error": str(e)})
            except Exception:
                pass
            continue
    return {"status": "ok", "applied": applied, "errors": errors}


@bp.get("/admin/client_columns")
def admin_client_columns():
    rows = db.session.execute(text("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'client'
        ORDER BY ordinal_position
    """)).mappings().all()
    return {"columns": [dict(r) for r in rows]}


# Documentos de clientes
@bp.post("/clientes/<int:client_id>/docs/upload")
def clientes_docs_upload(client_id: int):
    client = Client.query.get_or_404(client_id)
    files = request.files.getlist("documents")
    if not files:
        return redirect(url_for("main.clientes"))
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    upload_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET")
    for f in files:
        try:
            # Ignorar inputs vacíos
            if not f or not getattr(f, 'filename', None):
                continue
            # Leer contenido una vez para DB y/o Cloudinary
            content = f.read() or b""
            fname = f.filename or "documento"
            mtype = f.mimetype or "application/octet-stream"
            size = len(content)
            if size <= 0:
                continue
            url = ""
            if content and cloud_name and upload_preset:
                try:
                    r = requests.post(
                        f"https://api.cloudinary.com/v1_1/{cloud_name}/auto/upload",
                        data={"upload_preset": upload_preset},
                        files={"file": (fname, content, mtype)},
                        timeout=30,
                    )
                    if r.ok:
                        j = r.json()
                        url = j.get("secure_url") or j.get("url") or ""
                except Exception:
                    url = ""
            category = (request.form.get("category") or "CONSTANCIA").upper()
            doc = ClientDocument(client_id=client.id, category=category, filename=fname, filepath=url or "", data=content, mimetype=mtype, size=size)
            db.session.add(doc)
        except Exception:
            continue
    db.session.commit()
    # Respuesta AJAX: devolver documentos actualizados
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"
    if wants_json:
        docs = (
            ClientDocument.query.filter_by(client_id=client.id)
            .order_by(ClientDocument.uploaded_at.desc())
            .all()
        )
        return jsonify([
            {
                "id": d.id,
                "category": getattr(d, "category", None),
                "filename": d.filename,
                "download_url": url_for("main.clientes_docs_download", client_id=client.id, doc_id=d.id),
                "delete_url": url_for("main.clientes_docs_delete", client_id=client.id, doc_id=d.id),
                "uploaded_at": (d.uploaded_at.isoformat() if d.uploaded_at else None),
            }
            for d in docs
        ])
    return redirect(url_for("main.clientes_edit_view", client_id=client.id))


@bp.post("/clientes/<int:client_id>/docs/<int:doc_id>/delete")
def clientes_docs_delete(client_id: int, doc_id: int):
    doc = ClientDocument.query.filter_by(id=doc_id, client_id=client_id).first_or_404()
    db.session.delete(doc)
    db.session.commit()
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"
    if wants_json:
        docs = (
            ClientDocument.query.filter_by(client_id=client_id)
            .order_by(ClientDocument.uploaded_at.desc())
            .all()
        )
        return jsonify([
            {
                "id": d.id,
                "category": getattr(d, "category", None),
                "filename": d.filename,
                "download_url": url_for("main.clientes_docs_download", client_id=client_id, doc_id=d.id),
                "delete_url": url_for("main.clientes_docs_delete", client_id=client_id, doc_id=d.id),
                "uploaded_at": (d.uploaded_at.isoformat() if d.uploaded_at else None),
            }
            for d in docs
        ])
    return redirect(url_for("main.clientes_edit_view", client_id=client_id))

@bp.get("/clientes/<int:client_id>/docs/<int:doc_id>/download")
def clientes_docs_download(client_id: int, doc_id: int):
    doc = ClientDocument.query.filter_by(id=doc_id, client_id=client_id).first_or_404()
    # Prioridad: si hay datos en DB, servirlos
    try:
        if getattr(doc, "data", None):
            return send_file(
                BytesIO(doc.data),
                mimetype=doc.mimetype or "application/octet-stream",
                as_attachment=False,
                download_name=doc.filename or "documento"
            )
    except Exception:
        pass
    if doc.filepath:
        return redirect(doc.filepath)
    abort(404)


# Gestión de vínculos desde Clientes (bilateral con Empresas)
@bp.post("/clientes/<int:client_id>/links/add")
def clientes_link_add(client_id: int):
    client = Client.query.get_or_404(client_id)
    company_id = request.form.get("company_id", type=int)
    status = request.form.get("status") or RelationStatus.TRABAJA.value
    comprobante_tipo = (request.form.get("comprobante_tipo") or "").upper() or None
    comp = Company.query.get_or_404(company_id)
    link = ClientCompanyLink.query.filter_by(client_id=client.id, company_id=comp.id).first()
    if not link:
        link = ClientCompanyLink(client_id=client.id, company_id=comp.id)
        db.session.add(link)
    link.status = RelationStatus(status)
    if comprobante_tipo in ("FACTURA", "REMITO"):
        link.comprobante_tipo = comprobante_tipo
    db.session.commit()
    return redirect(url_for("main.clientes", open_links=client.id))


@bp.post("/clientes/<int:client_id>/links/<int:link_id>/status")
def clientes_link_update(client_id: int, link_id: int):
    link = ClientCompanyLink.query.filter_by(id=link_id, client_id=client_id).first_or_404()
    status = request.form.get("status") or None
    if status:
        link.status = RelationStatus(status)
        db.session.commit()
    return redirect(url_for("main.clientes", open_links=client_id))


@bp.post("/clientes/<int:client_id>/links/<int:link_id>/delete")
def clientes_link_delete(client_id: int, link_id: int):
    link = ClientCompanyLink.query.filter_by(id=link_id, client_id=client_id).first_or_404()
    db.session.delete(link)
    db.session.commit()
    return redirect(url_for("main.clientes", open_links=client_id))


@bp.get("/clientes/<int:client_id>/editar")
def clientes_edit_view(client_id: int):
    client = Client.query.get_or_404(client_id)
    companies = Company.query.order_by(Company.nombre).all()
    branches = [r[0] for r in db.session.query(Client.sucursal).filter(Client.sucursal.isnot(None)).distinct().order_by(Client.sucursal).all()]
    return render_template("clientes_form.html", active="clientes", client=client, companies=companies, branches=branches)


@bp.post("/clientes/<int:client_id>/editar")
def clientes_update(client_id: int):
    obj = Client.query.get_or_404(client_id)
    obj.apellido = request.form.get("apellido", obj.apellido)
    obj.nombre = request.form.get("nombre", obj.nombre)
    obj.cuit = (request.form.get("cuit") or None)
    # sucursales múltiples
    branch_list = [b.strip() for b in request.form.getlist("branch_list") if (b or "").strip()]
    # compat: actualizar sucursal como primera de la lista
    obj.sucursal = (branch_list[0] if branch_list else None)
    # sincronizar tabla ClientBranch con la selección actual
    try:
        ClientBranch.query.filter_by(client_id=obj.id).delete()
        for nm in branch_list:
            if nm:
                db.session.add(ClientBranch(client_id=obj.id, nombre=nm))
    except Exception:
        pass
    obj.telefono = request.form.get("telefono") or None
    obj.provincia = (request.form.get("provincia") or None)
    obj.forma_pago_habitual = ((request.form.get("forma_pago_habitual") or "").strip().upper() or None)
    # Transporte (sección colapsable)
    obj.transporte_recomendado = (request.form.get("transporte_nombre") or None)
    obj.transporte_contacto = (request.form.get("transporte_contacto") or None)
    # Mails múltiples
    mail_single = (request.form.get("mail") or "").strip()
    obj.mail = (mail_single or None)
    # Direcciones múltiples con provincia, nota, horarios
    delivery_names = [d.strip() for d in request.form.getlist("delivery_name_list")]
    delivery_provincias = [d.strip() for d in request.form.getlist("delivery_provincia_list")]
    delivery_notas = [d.strip() for d in request.form.getlist("delivery_nota_list")]
    delivery_schedules = [d.strip() for d in request.form.getlist("delivery_schedule_list")]
    delivery_contacts = [d.strip() for d in request.form.getlist("delivery_contact_list")]
    delivery_phones = [d.strip() for d in request.form.getlist("delivery_phone_list")]
    # Personas (cumpleaños) con notas
    b_names = [v.strip() for v in request.form.getlist("birthday_name_list")]
    b_roles = [v.strip() for v in request.form.getlist("birthday_role_list")]
    b_dates = [v.strip() for v in request.form.getlist("birthday_date_list")]
    b_notas = [v.strip() for v in request.form.getlist("birthday_notas_list")]
    relacion = (request.form.get("relacion") or "").strip() or None
    fecha_inc = request.form.get("fecha_incorporacion") or None
    obj.fecha_incorporacion = date.fromisoformat(fecha_inc) if fecha_inc else obj.fecha_incorporacion
    # Sincronizar direcciones (lugares de entrega) con provincia, nota, horarios
    try:
        ClientDeliveryPlace.query.filter_by(client_id=obj.id).delete()
        max_len = max(len(delivery_names), len(delivery_provincias), len(delivery_notas), len(delivery_schedules), len(delivery_contacts), len(delivery_phones)) if (delivery_names or delivery_provincias or delivery_notas or delivery_schedules or delivery_contacts or delivery_phones) else 0
        for idx in range(max_len):
            nm = (delivery_names[idx] if idx < len(delivery_names) else "").strip()
            pv = (delivery_provincias[idx] if idx < len(delivery_provincias) else "").strip()
            nt = (delivery_notas[idx] if idx < len(delivery_notas) else "").strip()
            hs = (delivery_schedules[idx] if idx < len(delivery_schedules) else "").strip()
            ct = (delivery_contacts[idx] if idx < len(delivery_contacts) else "").strip()
            ph = (delivery_phones[idx] if idx < len(delivery_phones) else "").strip()
            if not (nm or pv or nt or hs or ct or ph):
                continue
            db.session.add(ClientDeliveryPlace(client_id=obj.id, nombre=nm or "-", provincia=pv or None, nota=nt or None, horario=hs or None, contacto=ct or None, telefono=ph or None))
    except Exception:
        db.session.rollback()
    # Sincronizar personas (cumpleaños) con notas
    try:
        ClientBirthday.query.filter_by(client_id=obj.id).delete()
        max_len_b = max(len(b_names), len(b_roles), len(b_dates), len(b_notas)) if (b_names or b_roles or b_dates or b_notas) else 0
        for idx in range(max_len_b):
            nm = (b_names[idx] if idx < len(b_names) else "").strip()
            rl = (b_roles[idx] if idx < len(b_roles) else "").strip()
            dt_raw = (b_dates[idx] if idx < len(b_dates) else "").strip()
            nt = (b_notas[idx] if idx < len(b_notas) else "").strip()
            if not (nm or rl or dt_raw or nt):
                continue
            fecha = None
            if dt_raw:
                try:
                    fecha = date.fromisoformat(dt_raw) if "-" in dt_raw else None
                except Exception:
                    fecha = None
            db.session.add(ClientBirthday(client_id=obj.id, nombre=nm or "-", puesto=rl or None, fecha=fecha, notas=nt or None))
    except Exception:
        pass
    db.session.commit()
    # Aplicar estado: si vino empresa, crear/actualizar vínculo; si no, aplicar a todos
    company_id = request.form.get("company_id", type=int)
    if relacion and company_id:
        try:
            st = RelationStatus(relacion)
            comp = Company.query.get(company_id)
            if comp:
                link = ClientCompanyLink.query.filter_by(client_id=obj.id, company_id=comp.id).first()
                if not link:
                    link = ClientCompanyLink(client_id=obj.id, company_id=comp.id, status=st)
                    db.session.add(link)
                else:
                    link.status = st
                db.session.commit()
        except Exception:
            pass
    elif relacion:
        try:
            st = RelationStatus(relacion)
            for l in obj.links:
                l.status = st
            db.session.commit()
        except Exception:
            pass

    # Sincronizar empresas confirmadas si vinieron company_ids desde el formulario
    company_ids = request.form.getlist("company_ids", type=int)
    if company_ids:
        try:
            keep_ids = set(company_ids)
            existing = {l.company_id: l for l in obj.links}
            for l in list(obj.links):
                if l.company_id not in keep_ids:
                    db.session.delete(l)
            for coid in keep_ids:
                if coid in existing:
                    continue
                comp = Company.query.get(coid)
                if not comp:
                    continue
                db.session.add(ClientCompanyLink(client_id=obj.id, company_id=coid, status=RelationStatus.TRABAJA, comprobante_tipo="FACTURA"))
            db.session.commit()
        except Exception:
            db.session.rollback()
    return redirect(url_for("main.clientes"))


@bp.post("/clientes/<int:client_id>/delete")
def clientes_delete(client_id: int):
    obj = Client.query.get_or_404(client_id)
    db.session.delete(obj)
    db.session.commit()
    return redirect(url_for("main.clientes"))


@bp.post("/clientes/<int:client_id>/edit")
def clientes_edit(client_id: int):
    obj = Client.query.get_or_404(client_id)
    obj.apellido = request.form.get("apellido", obj.apellido)
    obj.nombre = request.form.get("nombre", obj.nombre)
    obj.sucursal = request.form.get("sucursal") or None
    obj.telefono = request.form.get("telefono") or None
    obj.mail = request.form.get("mail") or None
    fecha_inc = request.form.get("fecha_incorporacion") or None
    obj.fecha_incorporacion = date.fromisoformat(fecha_inc) if fecha_inc else obj.fecha_incorporacion
    db.session.commit()
    return redirect(url_for("main.clientes"))


@bp.get("/api/clientes")
def api_clientes():
    q = (request.args.get("q") or "").strip().lower()
    base = Client.query
    if q:
        base = base.filter((Client.apellido + " " + Client.nombre).ilike(f"%{q}%"))
    res = [{"id": c.id, "label": f"{c.apellido} {c.nombre}"} for c in base.order_by(Client.apellido).limit(20)]
    return jsonify(res)


@bp.get("/api/clientes/<int:client_id>/empresas")
def api_client_companies(client_id: int):
    c = Client.query.get_or_404(client_id)
    rows = []
    for l in c.links:
        if l.company:
            rows.append({
                "id": l.company.id,
                "label": l.company.nombre,
                "mail_pedido": l.company.mail_pedido or "",
                "mail_pago": l.company.mail_pago or "",
                "pedido_estandar_recomendado": l.company.pedido_estandar_recomendado or "",
                "comprobante_tipo": (getattr(l, "comprobante_tipo", None) or "FACTURA"),
                "forma_pago_default": (l.company.forma_pago_default or ""),
                "plazo_pago_promedio_dias": l.company.plazo_pago_promedio_dias,
                "status": getattr(l.status, "value", str(l.status))
            })
    # Sort by name
    rows.sort(key=lambda r: r["label"].lower())
    return jsonify(rows)


@bp.get("/empresas")
def empresas():
    q = (request.args.get("q") or "").strip()
    base = Company.query
    if q:
        ilike = f"%{q}%"
        base = base.filter((Company.marca.ilike(ilike)) | (Company.nombre.ilike(ilike)))
    items = base.order_by(Company.marca.nullslast(), Company.nombre).all()
    clients = Client.query.order_by(Client.apellido, Client.nombre).all()
    now_dt = datetime.utcnow()
    alerts_by_company = {}
    total_active_alerts = 0
    try:
        all_alerts = _compute_alerts_for_all_clients(now_dt)
    except Exception:
        all_alerts = []
    for a in all_alerts:
        coid = a.get("company_id")
        if not coid:
            continue
        alerts_by_company.setdefault(coid, []).append(a)
        total_active_alerts += 1
    return render_template(
        "empresas.html",
        active="empresas",
        items=items,
        clients=clients,
        q=q,
        alerts_by_company=alerts_by_company,
        total_active_alerts=total_active_alerts,
    )


@bp.get("/empresas/nueva")
def empresas_new():
    clients = Client.query.order_by(Client.apellido, Client.nombre).all()
    return render_template("empresas_form.html", active="empresas", clients=clients)


@bp.post("/empresas/nueva")
def empresas_create():
    nombre = request.form.get("nombre", "").strip()
    marca = (request.form.get("marca", "") or "").strip() or None
    demora = request.form.get("demora", type=int)
    plazo = request.form.get("plazo", type=int)
    plazo_usual = (request.form.get("plazo_usual") or "").strip() or None
    pedido_estandar_recomendado = (request.form.get("pedido_estandar_recomendado") or "").strip() or None
    forma_pago_default = ((request.form.get("forma_pago_default") or "").strip().upper() or None)
    mail_pedido_list = [m.strip() for m in request.form.getlist("mail_pedido_list") if (m or "").strip()]
    mail_pedido_single = (request.form.get("mail_pedido", "") or "").strip()
    mail_pedido = ", ".join(mail_pedido_list) if mail_pedido_list else (mail_pedido_single or None)
    mail_pago_list = [m.strip() for m in request.form.getlist("mail_pago_list") if (m or "").strip()]
    mail_pago_single = (request.form.get("mail_pago", "") or "").strip()
    mail_pago = ", ".join(mail_pago_list) if mail_pago_list else (mail_pago_single or None)
    cuit = (request.form.get("cuit", "") or "").strip() or None
    notas = (request.form.get("notas", "") or "").strip() or None
    cuenta_bancaria_notas = (request.form.get("cuenta_bancaria_notas", "") or "").strip() or None
    company = Company(
        nombre=nombre or "-",
        marca=marca,
        demora_despacho_promedio_dias=demora or 0,
        plazo_pago_promedio_dias=plazo or 30,
        plazo_usual=plazo_usual,
        pedido_estandar_recomendado=pedido_estandar_recomendado,
        forma_pago_default=forma_pago_default,
        mail_pedido=mail_pedido,
        mail_pago=mail_pago,
        cuit=cuit,
        notas=notas,
        cuenta_bancaria_notas=cuenta_bancaria_notas,
    )
    db.session.add(company)
    db.session.flush()
    client_ids = request.form.getlist("client_ids", type=int)
    if client_ids:
        for cid in client_ids:
            c = Client.query.get(cid)
            if not c:
                continue
            link = ClientCompanyLink.query.filter_by(client_id=cid, company_id=company.id).first()
            if not link:
                link = ClientCompanyLink(client_id=cid, company_id=company.id, status=RelationStatus.TRABAJA, comprobante_tipo="FACTURA")
                db.session.add(link)
    db.session.commit()
    return redirect(url_for("main.empresas"))


@bp.post("/empresas/<int:company_id>/share_to_client")
def empresas_share_to_client(company_id: int):
    """Enviar mail desde una empresa a un cliente con adjuntos (constancias y catálogos)."""
    company = Company.query.get_or_404(company_id)
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id") or request.form.get("client_id", type=int)
    comment = (data.get("comment") or request.form.get("comment") or "").strip()
    body_override = (data.get("body") or request.form.get("body") or "").strip()

    if not client_id:
        return jsonify({"ok": False, "error": "client_id requerido"}), 400

    client = Client.query.get_or_404(client_id)
    if not (client.mail or "").strip():
        return jsonify({"ok": False, "error": "El cliente no tiene mails configurados"}), 400

    # Preparar destinatarios
    to_all = [m.strip() for m in (client.mail or "").split(",") if m.strip()]
    if not to_all:
        return jsonify({"ok": False, "error": "No se pudieron interpretar los mails del cliente"}), 400

    # Construir asunto y cuerpo (permitiendo override explícito desde frontend)
    razon_social = (company.nombre or "").strip()
    marca = (company.marca or "").strip()
    cuit = (company.cuit or "").strip()
    cuenta_bancaria = (company.cuenta_bancaria_notas or "").strip()

    subject = f"Información de empresa - {razon_social or marca or 'Empresa'}"

    if body_override:
        body = body_override
    else:
        body_lines = []
        body_lines.append("INFORMACIÓN DE EMPRESA")
        body_lines.append("")
        body_lines.append(f"Razón social: {razon_social or '-'}")
        body_lines.append(f"Marca: {marca or '-'}")
        body_lines.append(f"CUIL/CUIT: {cuit or '-'}")
        body_lines.append("")
        body_lines.append("Constancias AFIP y Rentas: adjuntas en este correo (si corresponden).")
        body_lines.append("Catálogos: adjuntos en este correo (si corresponden).")
        body_lines.append("")
        body_lines.append("Cuenta bancaria:")
        body_lines.append(cuenta_bancaria or "-")
        if comment:
            body_lines.append("")
            body_lines.append("Comentario adicional:")
            body_lines.append(comment)

        body = "\n".join(body_lines)

    # Armar mensaje
    msg = EmailMessage()
    msg["Subject"] = subject
    from_addr = os.getenv("GMAIL_FROM") or os.getenv("GMAIL_USER")
    if not from_addr:
        return jsonify({"ok": False, "error": "Configurar GMAIL_USER o GMAIL_FROM en variables de entorno"}), 500
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_all)
    msg.set_content(body)

    # Adjuntar solo el documento más reciente de cada tipo (CONSTANCIA / CATALOGO)
    docs = (
        CompanyDocument.query.filter_by(company_id=company.id)
        .order_by(CompanyDocument.uploaded_at.desc())
        .all()
    )
    attached_by_cat = set()
    for d in docs:
        cat = (d.category or "").upper()
        if cat not in ("CONSTANCIA", "CATALOGO"):
            continue
        if cat in attached_by_cat:
            continue
        content = None
        try:
            if getattr(d, "data", None):
                content = d.data
            elif d.filepath:
                try:
                    r = requests.get(d.filepath, timeout=15)
                    if r.ok:
                        content = r.content
                except Exception:
                    content = None
            if not content:
                continue
            maintype = "application"
            subtype = "octet-stream"
            if d.mimetype and "/" in d.mimetype:
                maintype, subtype = d.mimetype.split("/", 1)
            filename = d.filename or f"documento_{d.id}"
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
            attached_by_cat.add(cat)
        except Exception:
            continue

    # Enviar por SMTP (Gmail) o modo fake según entorno
    gmail_user = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_PASSWORD")

    # Modo desarrollo / sin salida a Internet: no envía realmente, solo loguea
    mail_fake = (os.getenv("MAIL_FAKE") or "").strip().lower() in {"1", "true", "yes", "on"}
    if mail_fake:
        print("[empresas_share_to_client] MAIL_FAKE activo - no se envía mail real")
        print("[empresas_share_to_client] From:", msg["From"], "To:", msg["To"])
        print("[empresas_share_to_client] Subject:", msg["Subject"])
        # No mostramos el cuerpo completo por consola para evitar ruido excesivo
        return jsonify({"ok": True, "fake": True})

    if not gmail_user or not gmail_password:
        return jsonify({"ok": False, "error": "Configurar GMAIL_USER y GMAIL_PASSWORD en variables de entorno"}), 500

    try:
        print("[empresas_share_to_client] Enviando mail a:", to_all)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(gmail_user, gmail_password)
            smtp.send_message(msg)
        print("[empresas_share_to_client] Mail enviado correctamente")
    except Exception as e:
        # Mensajes más amigables para el frontend
        err_text = str(e) or "Error desconocido enviando el mail"
        if "Network is unreachable" in err_text:
            user_msg = "No se pudo conectar al servidor de correo (sin conexión o bloqueado)."
        else:
            user_msg = err_text
        print("[empresas_share_to_client] Error al enviar mail:", err_text)
        return jsonify({"ok": False, "error": user_msg}), 500

    return jsonify({"ok": True})

# Documentos de empresas (Constancias, Catálogos)
@bp.post("/empresas/<int:company_id>/docs/upload")
def empresas_docs_upload(company_id: int):
    company = Company.query.get_or_404(company_id)
    files = request.files.getlist("documents")
    category = (request.form.get("category") or "").upper() or "CONSTANCIA"
    if not files:
        return redirect(url_for("main.empresas_edit_view", company_id=company.id))
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    upload_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET")
    # Límite total de tamaño en MB para evitar problemas de envío por mail (Gmail)
    try:
        max_mb = int(os.getenv("MAX_COMPANY_DOCS_MB", "20"))
    except Exception:
        max_mb = 20
    max_bytes = max_mb * 1024 * 1024
    try:
        existing_total = (
            db.session.query(func.coalesce(func.sum(CompanyDocument.size), 0))
            .filter(CompanyDocument.company_id == company.id)
            .scalar()
            or 0
        )
    except Exception:
        existing_total = 0
    skipped_by_limit = False
    for f in files:
        try:
            if not f or not getattr(f, "filename", None):
                continue
            content = f.read() or b""
            fname = f.filename or "documento"
            mtype = f.mimetype or "application/octet-stream"
            size = len(content)
            if size <= 0:
                continue
            # Si este archivo hace que se supere el límite total permitido, lo ignoramos
            if max_bytes and (existing_total + size) > max_bytes:
                skipped_by_limit = True
                continue
            url = ""
            if content and cloud_name and upload_preset:
                try:
                    r = requests.post(
                        f"https://api.cloudinary.com/v1_1/{cloud_name}/auto/upload",
                        data={"upload_preset": upload_preset},
                        files={"file": (fname, content, mtype)},
                        timeout=30,
                    )
                    if r.ok:
                        j = r.json()
                        url = j.get("secure_url") or j.get("url") or ""
                except Exception:
                    url = ""
            doc = CompanyDocument(
                company_id=company.id,
                category=category,
                filename=fname,
                filepath=url or "",
                data=content,
                mimetype=mtype,
                size=size,
            )
            db.session.add(doc)
            existing_total += size
        except Exception:
            continue
    db.session.commit()
    if skipped_by_limit:
        return redirect(url_for("main.empresas_edit_view", company_id=company.id, upload_limit=1, upload_limit_mb=max_mb))
    return redirect(url_for("main.empresas_edit_view", company_id=company.id))


@bp.post("/empresas/<int:company_id>/docs/<int:doc_id>/delete")
def empresas_docs_delete(company_id: int, doc_id: int):
    doc = CompanyDocument.query.filter_by(id=doc_id, company_id=company_id).first_or_404()
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for("main.empresas_edit_view", company_id=company_id))


@bp.get("/empresas/<int:company_id>/docs/<int:doc_id>/download")
def empresas_docs_download(company_id: int, doc_id: int):
    doc = CompanyDocument.query.filter_by(id=doc_id, company_id=company_id).first_or_404()
    try:
        if getattr(doc, "data", None):
            return send_file(
                BytesIO(doc.data),
                mimetype=doc.mimetype or "application/octet-stream",
                as_attachment=False,
                download_name=doc.filename or "documento",
            )
    except Exception:
        pass
    if doc.filepath:
        return redirect(doc.filepath)
    abort(404)


@bp.get("/estado-clientes")
def estado_clientes():
    clients = Client.query.order_by(Client.apellido, Client.nombre).all()
    return render_template("estado_clientes.html", active="clientes", clients=clients)


@bp.post("/estado-clientes/links/<int:link_id>/status")
def estado_clientes_update(link_id: int):
    link = ClientCompanyLink.query.get_or_404(link_id)
    status = request.form.get("status") or None
    if status:
        link.status = RelationStatus(status)
        db.session.commit()
    return redirect(url_for("main.estado_clientes"))


@bp.post("/estado-clientes/<int:client_id>/status")
def estado_clientes_update_all(client_id: int):
    client = Client.query.get_or_404(client_id)
    status = request.form.get("status") or None
    if status:
        new_status = RelationStatus(status)
        for l in client.links:
            l.status = new_status
        db.session.commit()
    return redirect(url_for("main.estado_clientes"))


@bp.get("/empresas/<int:company_id>/editar")
def empresas_edit_view(company_id: int):
    company = Company.query.get_or_404(company_id)
    clients = Client.query.order_by(Client.apellido, Client.nombre).all()
    return render_template("empresas_form.html", active="empresas", company=company, clients=clients)


@bp.post("/empresas/<int:company_id>/editar")
def empresas_update(company_id: int):
    obj = Company.query.get_or_404(company_id)
    obj.nombre = request.form.get("nombre", obj.nombre)
    obj.marca = (request.form.get("marca") or obj.marca)
    obj.demora_despacho_promedio_dias = request.form.get("demora", type=int) or obj.demora_despacho_promedio_dias
    obj.plazo_pago_promedio_dias = request.form.get("plazo", type=int) or obj.plazo_pago_promedio_dias
    obj.plazo_usual = (request.form.get("plazo_usual") or "").strip() or None
    obj.pedido_estandar_recomendado = (request.form.get("pedido_estandar_recomendado") or "").strip() or None
    obj.forma_pago_default = ((request.form.get("forma_pago_default") or "").strip().upper() or None)
    mail_pedido_list = [m.strip() for m in request.form.getlist("mail_pedido_list") if (m or "").strip()]
    mail_pedido_single = (request.form.get("mail_pedido") or "").strip()
    obj.mail_pedido = (", ".join(mail_pedido_list) if mail_pedido_list else (mail_pedido_single or None))
    mail_pago_list = [m.strip() for m in request.form.getlist("mail_pago_list") if (m or "").strip()]
    mail_pago_single = (request.form.get("mail_pago") or "").strip()
    obj.mail_pago = (", ".join(mail_pago_list) if mail_pago_list else (mail_pago_single or None))
    obj.cuit = (request.form.get("cuit") or None)
    obj.notas = (request.form.get("notas") or None)
    obj.cuenta_bancaria_notas = (request.form.get("cuenta_bancaria_notas") or None)

    # Sincronizar clientes vinculados si vinieron client_ids desde el formulario
    client_ids = request.form.getlist("client_ids", type=int)
    if client_ids:
        keep_ids = set(client_ids)
        # Mapear vínculos existentes por client_id
        existing = {l.client_id: l for l in obj.links}
        # Eliminar vínculos que ya no están seleccionados
        for l in list(obj.links):
            if l.client_id not in keep_ids:
                db.session.delete(l)
        # Crear vínculos nuevos para los ids seleccionados que no existían
        for cid in keep_ids:
            if cid in existing:
                continue
            c = Client.query.get(cid)
            if not c:
                continue
            db.session.add(ClientCompanyLink(client_id=cid, company_id=obj.id, status=RelationStatus.TRABAJA, comprobante_tipo="FACTURA"))

    db.session.commit()
    return redirect(url_for("main.empresas"))


@bp.post("/empresas/<int:company_id>/links/add")
def empresas_link_add(company_id: int):
    company = Company.query.get_or_404(company_id)
    client_id = request.form.get("client_id", type=int)
    status = request.form.get("status") or RelationStatus.TRABAJA.value
    comprobante_tipo = (request.form.get("comprobante_tipo") or "").upper() or None
    descuento_raw = (request.form.get("descuento") or "").strip()
    client = Client.query.get_or_404(client_id)
    link = ClientCompanyLink.query.filter_by(client_id=client.id, company_id=company.id).first()
    if not link:
        link = ClientCompanyLink(client_id=client.id, company_id=company.id)
        db.session.add(link)
    link.status = RelationStatus(status)
    if comprobante_tipo in ("FACTURA", "REMITO"):
        link.comprobante_tipo = comprobante_tipo
    if not getattr(link, "comprobante_tipo", None):
        link.comprobante_tipo = "FACTURA"
    if descuento_raw != "":
        try:
            val = int(round(float(descuento_raw)))
            if val < 0:
                val = 0
            if val > 100:
                val = 100
            link.descuento = val
        except Exception:
            pass
    db.session.commit()
    return redirect(url_for("main.empresas", open_links=company.id))


@bp.post("/empresas/<int:company_id>/links/<int:link_id>/status")
def empresas_link_update(company_id: int, link_id: int):
    link = ClientCompanyLink.query.filter_by(id=link_id, company_id=company_id).first_or_404()
    status = request.form.get("status") or None
    comprobante_tipo = (request.form.get("comprobante_tipo") or "").upper() or None
    descuento_raw = (request.form.get("descuento") or "").strip()
    if status:
        link.status = RelationStatus(status)
    if comprobante_tipo in ("FACTURA", "REMITO"):
        link.comprobante_tipo = comprobante_tipo
    if descuento_raw != "":
        try:
            val = int(round(float(descuento_raw)))
            if val < 0:
                val = 0
            if val > 100:
                val = 100
            link.descuento = val
        except Exception:
            pass
    db.session.commit()
    return redirect(url_for("main.empresas", open_links=company_id))


@bp.post("/empresas/<int:company_id>/links/<int:link_id>/delete")
def empresas_link_delete(company_id: int, link_id: int):
    link = ClientCompanyLink.query.filter_by(id=link_id, company_id=company_id).first_or_404()
    db.session.delete(link)
    db.session.commit()
    return redirect(url_for("main.empresas", open_links=company_id))


@bp.post("/empresas/<int:company_id>/delete")
def empresas_delete(company_id: int):
    obj = Company.query.get_or_404(company_id)
    db.session.delete(obj)
    db.session.commit()
    return redirect(url_for("main.empresas"))


@bp.post("/empresas/<int:company_id>/edit")
def empresas_edit(company_id: int):
    obj = Company.query.get_or_404(company_id)
    obj.nombre = request.form.get("nombre", obj.nombre)
    obj.demora_despacho_promedio_dias = request.form.get("demora", type=int) or obj.demora_despacho_promedio_dias
    obj.plazo_pago_promedio_dias = request.form.get("plazo", type=int) or obj.plazo_pago_promedio_dias
    obj.mail_pedido = (request.form.get("mail_pedido") or None)
    obj.mail_pago = (request.form.get("mail_pago") or None)
    obj.plazo_usual = (request.form.get("plazo_usual") or "").strip() or None
    obj.pedido_estandar_recomendado = (request.form.get("pedido_estandar_recomendado") or "").strip() or None
    obj.forma_pago_default = ((request.form.get("forma_pago_default") or "").strip().upper() or None)
    db.session.commit()
    return redirect(url_for("main.empresas"))


@bp.get("/api/empresas")
def api_empresas():
    q = (request.args.get("q") or "").strip().lower()
    base = Company.query
    if q:
        base = base.filter(Company.nombre.ilike(f"%{q}%"))
    res = [{"id": e.id, "label": e.nombre} for e in base.order_by(Company.nombre).limit(20)]
    return jsonify(res)


@bp.get("/pedidos")
def pedidos():
    return render_template("pedidos.html", active="pedidos", today=date.today().isoformat(),
                           clientes=Client.query.order_by(Client.apellido, Client.nombre).all())


@bp.post("/pedidos")
def pedidos_create():
    # Get selected client/company IDs from the form
    client_id = request.form.get("client_id", type=int)
    company_id = request.form.get("company_id", type=int)
    sucursal = request.form.get("sucursal") or None
    branch_id = request.form.get("branch_id", type=int)
    nota = request.form.get("nota") or None
    descripcion = request.form.get("descripcion") or None
    # fechas proporcionadas por el formulario
    fecha_compra_raw = (request.form.get("fecha_compra") or "").strip()
    fecha_entrega_estimada_raw = (request.form.get("fecha_entrega_estimada") or "").strip()
    # tipo de comprobante (FACTURA / REMITO)
    tipo_comprobante = (request.form.get("tipo_comprobante") or "").strip().upper() or None
    precio_final = request.form.get("precio_final", type=float)
    forma_pago = (request.form.get("forma_pago") or "").strip().upper() or None
    plazo_pago_dias = request.form.get("plazo_pago_dias", type=int)

    if not client_id:
        abort(400, "Debe seleccionar un cliente")
    if not company_id:
        abort(400, "Debe seleccionar una empresa vinculada al cliente")
    if tipo_comprobante not in ("FACTURA", "REMITO"):
        abort(400, "Debe seleccionar si es Factura o Remito")

    client = Client.query.get_or_404(client_id)
    company = Company.query.get_or_404(company_id)

    # Plazo de pago del pedido: default empresa pero editable
    if plazo_pago_dias is None:
        try:
            plazo_pago_dias = int(company.plazo_pago_promedio_dias or 0)
        except Exception:
            plazo_pago_dias = None

    forma_pago_enum = None
    if forma_pago:
        try:
            forma_pago_enum = PaymentMethod(forma_pago)
        except Exception:
            forma_pago_enum = None

    # Si no se envió branch_id, tomar la primera sucursal del cliente como default
    if not branch_id:
        first_branch = ClientBranch.query.filter_by(client_id=client.id).order_by(ClientBranch.id.asc()).first()
        if first_branch:
            branch_id = first_branch.id
            # también setear texto sucursal si no vino
            if not sucursal:
                sucursal = first_branch.nombre

    order = Order(client=client, company=company, sucursal=sucursal, branch_id=branch_id, nota=nota, descripcion=descripcion,
                  precio_final=precio_final, forma_pago=forma_pago_enum, tipo_comprobante=tipo_comprobante,
                  plazo_pago_dias=plazo_pago_dias,
                  demora_despacho_promedio_dias=company.demora_despacho_promedio_dias,
                  mail_pedido=company.mail_pedido)
    db.session.add(order)
    db.session.flush()

    # Regla: al crear pedido, si la relación estaba A_INCORPORAR pasa a TRABAJA
    try:
        link = ClientCompanyLink.query.filter_by(client_id=client.id, company_id=company.id).first()
        if link and link.status == RelationStatus.A_INCORPORAR:
            link.status = RelationStatus.TRABAJA
    except Exception:
        pass

    # Create logistics record
    # fecha_compra: usar la provista (YYYY-MM-DD) o fallback a ahora
    try:
        fecha_compra = datetime.fromisoformat(fecha_compra_raw) if fecha_compra_raw else datetime.utcnow()
    except Exception:
        fecha_compra = datetime.utcnow()
    # fecha_entrega_estimada: usar la provista o calcular por demora promedio
    try:
        fecha_estimada = datetime.fromisoformat(fecha_entrega_estimada_raw) if fecha_entrega_estimada_raw else (fecha_compra + timedelta(days=company.demora_despacho_promedio_dias or 0))
    except Exception:
        fecha_estimada = fecha_compra + timedelta(days=company.demora_despacho_promedio_dias or 0)
    logistics = LogisticsStatus(order_id=order.id, fecha_compra=fecha_compra,
                                fecha_entrega_estimada=fecha_estimada, nota=nota, descripcion=descripcion,
                                precio=precio_final, forma_pago=order.forma_pago)
    db.session.add(logistics)
    files = request.files.getlist("attachments")
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    upload_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET")
    if files and cloud_name and upload_preset:
        for f in files:
            try:
                r = requests.post(
                    f"https://api.cloudinary.com/v1_1/{cloud_name}/auto/upload",
                    data={"upload_preset": upload_preset, "resource_type": "auto"},
                    files={"file": (f.filename, f.stream, f.mimetype)},
                )
                if r.ok:
                    url = r.json().get("secure_url") or r.json().get("url")
                    if url:
                        db.session.add(OrderAttachment(order_id=order.id, url=url))
            except Exception:
                pass
    db.session.commit()
    return redirect(url_for("main.status"))


@bp.get("/status")
def status():
    q = LogisticsStatus.query.join(Order)
    estados = request.args.getlist("status")
    tipos = request.args.getlist("tipo")  # FACTURA / REMITO
    desde = request.args.get("from")
    hasta = request.args.get("to")
    if estados:
        estado_map = {
            "EN_CAMINO": lambda s: s.fecha_entrega_efectiva.is_(None) and (s.fecha_entrega_estimada.is_(None) | (LogisticsStatus.fecha_entrega_estimada >= datetime.utcnow())),
            "ATRASADO": lambda s: s.fecha_entrega_efectiva.is_(None) and (LogisticsStatus.fecha_entrega_estimada < datetime.utcnow()),
            "ENTREGADO": lambda s: LogisticsStatus.fecha_entrega_efectiva.isnot(None),
        }
        conds = []
        now = datetime.utcnow()
        if "EN_CAMINO" in estados:
            conds.append((LogisticsStatus.fecha_entrega_efectiva.is_(None)) & ((LogisticsStatus.fecha_entrega_estimada.is_(None)) | (LogisticsStatus.fecha_entrega_estimada >= now)))
        if "ATRASADO" in estados:
            conds.append((LogisticsStatus.fecha_entrega_efectiva.is_(None)) & (LogisticsStatus.fecha_entrega_estimada < now))
        if "ENTREGADO" in estados:
            conds.append(LogisticsStatus.fecha_entrega_efectiva.isnot(None))
        if conds:
            from sqlalchemy import or_
            q = q.filter(or_(*conds))
    if tipos:
        q = q.filter(Order.tipo_comprobante.in_(tipos))
    if desde:
        try:
            d = datetime.fromisoformat(desde)
            q = q.filter(LogisticsStatus.fecha_compra >= d)
        except Exception:
            pass
    if hasta:
        try:
            h = datetime.fromisoformat(hasta)
            q = q.filter(LogisticsStatus.fecha_compra <= h)
        except Exception:
            pass
    # Mostrar primero el último agregado (proxy: id descendente)
    items = q.order_by(LogisticsStatus.id.desc()).all()
    return render_template("status.html", active="status", items=items)


@bp.post("/status/<int:order_id>/entregar")
def status_mark_entregado(order_id: int):
    logistics = LogisticsStatus.query.filter_by(order_id=order_id).first_or_404()
    if not logistics.fecha_entrega_efectiva:
        logistics.fecha_entrega_efectiva = datetime.utcnow()
        # Create or update collection
        coll = Collection.query.filter_by(order_id=order_id).first()
        if not coll:
            coll = Collection(order_id=order_id)
            db.session.add(coll)
        coll.fecha_entrega_efectiva = logistics.fecha_entrega_efectiva
        try:
            plazo_days = None
            if getattr(logistics.order, "plazo_pago_dias", None) is not None:
                plazo_days = int(logistics.order.plazo_pago_dias or 0)
            elif logistics.order.company and logistics.order.company.plazo_pago_promedio_dias is not None:
                plazo_days = int(logistics.order.company.plazo_pago_promedio_dias or 0)
            else:
                plazo_days = 30
        except Exception:
            plazo_days = 30
        coll.fecha_pago_estimada = logistics.fecha_entrega_efectiva + timedelta(days=plazo_days)
        coll.monto = logistics.precio
        coll.forma_pago = logistics.forma_pago
        db.session.commit()
    return redirect(url_for("main.status"))


@bp.post("/status/<int:order_id>/update")
def status_update(order_id: int):
    logistics = LogisticsStatus.query.filter_by(order_id=order_id).first_or_404()
    precio = request.form.get("precio", type=float)
    forma_pago_raw = request.form.get("forma_pago")
    forma_pago = None if forma_pago_raw == "" else (PaymentMethod(forma_pago_raw) if forma_pago_raw else None)
    fecha_entrega_estimada_raw = (request.form.get("fecha_entrega_estimada") or "").strip()
    fecha_compra_raw = (request.form.get("fecha_compra") or "").strip()
    fecha_entrega_efectiva_raw = (request.form.get("fecha_entrega_efectiva") or "").strip()
    if precio is not None:
        logistics.precio = precio
    if forma_pago_raw is not None:
        logistics.forma_pago = forma_pago
    if fecha_compra_raw is not None:
        try:
            logistics.fecha_compra = datetime.fromisoformat(fecha_compra_raw) if fecha_compra_raw else None
        except Exception:
            pass
    if fecha_entrega_estimada_raw:
        try:
            logistics.fecha_entrega_estimada = datetime.fromisoformat(fecha_entrega_estimada_raw)
        except Exception:
            pass
    if fecha_entrega_efectiva_raw is not None:
        try:
            logistics.fecha_entrega_efectiva = datetime.fromisoformat(fecha_entrega_efectiva_raw) if fecha_entrega_efectiva_raw else None
        except Exception:
            pass
    # Mantener consistencia con cobranzas si existe registro
    coll = Collection.query.filter_by(order_id=order_id).first()
    if coll:
        if precio is not None:
            coll.monto = precio
        if forma_pago_raw is not None:
            coll.forma_pago = forma_pago
        if fecha_entrega_efectiva_raw is not None:
            try:
                coll.fecha_entrega_efectiva = datetime.fromisoformat(fecha_entrega_efectiva_raw) if fecha_entrega_efectiva_raw else None
            except Exception:
                pass
    else:
        # Si se cargó entrega efectiva manualmente, crear cobranzas para mantener conexión
        if fecha_entrega_efectiva_raw:
            try:
                coll = Collection(order_id=order_id)
                coll.fecha_entrega_efectiva = datetime.fromisoformat(fecha_entrega_efectiva_raw)
                try:
                    plazo_days = None
                    if getattr(logistics.order, "plazo_pago_dias", None) is not None:
                        plazo_days = int(logistics.order.plazo_pago_dias or 0)
                    elif logistics.order.company and logistics.order.company.plazo_pago_promedio_dias is not None:
                        plazo_days = int(logistics.order.company.plazo_pago_promedio_dias or 0)
                    else:
                        plazo_days = 30
                except Exception:
                    plazo_days = 30
                coll.fecha_pago_estimada = coll.fecha_entrega_efectiva + timedelta(days=plazo_days)
                coll.monto = logistics.precio
                coll.forma_pago = logistics.forma_pago
                db.session.add(coll)
            except Exception:
                pass
    # Mantener consistencia con la Orden
    order = Order.query.get(order_id)
    if order:
        if precio is not None:
            order.precio_final = precio
        if forma_pago_raw is not None:
            order.forma_pago = forma_pago
    db.session.commit()
    return redirect(url_for("main.status"))


@bp.get("/cobranzas")
def cobranzas():
    return redirect(url_for("main.deudas_pendientes"))


@bp.get("/deudas")
def deudas_pendientes():
    q = Collection.query.join(Order)
    estados = request.args.getlist("status")
    tipos = request.args.getlist("tipo")  # FACTURA / REMITO
    desde = request.args.get("from")
    hasta = request.args.get("to")
    if estados:
        from sqlalchemy import or_
        conds = []
        now = datetime.utcnow()
        if "A_COBRAR" in estados:
            conds.append((Collection.fecha_cobro_efectiva.is_(None)))
        if "COBRADO" in estados:
            conds.append(Collection.fecha_cobro_efectiva.isnot(None))
        if conds:
            q = q.filter(or_(*conds))
    if tipos:
        q = q.filter(Order.tipo_comprobante.in_(tipos))
    if desde:
        try:
            d = datetime.fromisoformat(desde)
            q = q.filter(Collection.fecha_pago_estimada >= d)
        except Exception:
            pass
    if hasta:
        try:
            h = datetime.fromisoformat(hasta)
            q = q.filter(Collection.fecha_pago_estimada <= h)
        except Exception:
            pass
    # Mostrar primero el último agregado (proxy: id descendente)
    items = q.order_by(Collection.id.desc()).all()
    return render_template(
        "cobranzas.html",
        active="deudas",
        items=items,
        now_date=date.today(),
    )


@bp.get("/cobranzas/nueva")
def nueva_cobranza():
    return render_template(
        "nueva_cobranza.html",
        active="deudas",
        today=date.today().isoformat(),
        clientes=Client.query.order_by(Client.apellido, Client.nombre).all(),
    )


@bp.get("/api/cobranzas/pedidos_pendientes")
def api_cobranzas_pedidos_pendientes():
    client_id = request.args.get("client_id", type=int)
    company_id = request.args.get("company_id", type=int)
    if not client_id or not company_id:
        return jsonify([])
    q = (
        Order.query
        .filter(Order.client_id == client_id)
        .filter(Order.company_id == company_id)
        .join(Collection)
        .filter(Collection.fecha_cobro_efectiva.is_(None))
        .order_by(Order.created_at.desc())
    )
    res = []
    for o in q.all():
        col = o.collection
        monto_val = 0.0
        try:
            candidates = []
            if col is not None and getattr(col, "monto", None) is not None:
                candidates.append((float(col.monto), "collection"))
            if getattr(o, "precio_final", None) is not None:
                candidates.append((float(o.precio_final), "order"))
            lg = getattr(o, "logistics", None)
            if lg is not None and getattr(lg, "precio", None) is not None:
                candidates.append((float(lg.precio), "logistics"))
            # Preferir un monto > 0; si no hay, caer al primero disponible (puede ser 0)
            picked = None
            for v, _src in candidates:
                if v is not None and v > 0:
                    picked = v
                    break
            if picked is None and candidates:
                picked = candidates[0][0]
            monto_val = float(picked or 0.0)
        except Exception:
            monto_val = 0.0
        res.append({
            "order_id": o.id,
            "created_at": (o.created_at.date().isoformat() if o.created_at else None),
            "fecha_entrega_efectiva": (col.fecha_entrega_efectiva.date().isoformat() if col and col.fecha_entrega_efectiva else None),
            "fecha_pago_estimada": (col.fecha_pago_estimada.date().isoformat() if col and col.fecha_pago_estimada else None),
            "monto": monto_val,
            "tipo_comprobante": o.tipo_comprobante,
            "nota": o.nota,
            "descripcion": o.descripcion,
        })
    return jsonify(res)


@bp.post("/cobranzas/nueva")
def nueva_cobranza_create():
    client_id = request.form.get("client_id", type=int)
    company_id = request.form.get("company_id", type=int)
    order_id = request.form.get("order_id", type=int)
    if not client_id or not company_id or not order_id:
        return redirect(url_for("main.nueva_cobranza"))

    o = Order.query.get_or_404(order_id)
    if o.client_id != client_id or o.company_id != company_id:
        abort(400)
    coll = Collection.query.filter_by(order_id=order_id).first()
    if not coll:
        coll = Collection(order_id=order_id)
        db.session.add(coll)

    # Payload: rows serialized as JSON-ish in repeated form fields
    kinds = request.form.getlist("row_kind")
    methods = request.form.getlist("row_method")
    amounts = request.form.getlist("row_amount")
    dues = request.form.getlist("row_due_date")
    notes = request.form.getlist("row_notes")

    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    upload_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET")
    files = request.files.getlist("row_attachment")

    # Clear existing draft rows for this order (to avoid duplicates on resubmit)
    try:
        CollectionPayment.query.filter_by(order_id=order_id).delete()
    except Exception:
        db.session.rollback()

    total_paid = 0.0
    total_credit = 0.0
    max_due = None

    row_count = max(len(kinds), len(methods), len(amounts), len(dues), len(notes), len(files))
    for i in range(row_count):
        kind = (kinds[i] if i < len(kinds) else "").strip().upper() or "PAYMENT"
        method = (methods[i] if i < len(methods) else "").strip() or None
        raw_amount = (amounts[i] if i < len(amounts) else "").strip()
        raw_due = (dues[i] if i < len(dues) else "").strip()
        row_note = (notes[i] if i < len(notes) else "").strip() or None
        f = files[i] if i < len(files) else None

        def _parse_amount(s: str) -> float:
            s = (s or "").strip()
            if not s:
                return 0.0
            s = s.replace(" ", "")
            # Soportar formatos: 1234.56 / 1234,56 / 1.234,56
            if "," in s and "." in s:
                # asume '.' miles y ',' decimal
                s = s.replace(".", "").replace(",", ".")
            elif "," in s:
                s = s.replace(",", ".")
            return float(s)

        try:
            amount_val = _parse_amount(raw_amount)
        except Exception:
            amount_val = 0.0
        if not amount_val and not method and not raw_due and not row_note and (not f or not getattr(f, "filename", "")):
            continue

        due_dt = None
        if raw_due:
            try:
                due_dt = datetime.fromisoformat(raw_due)
            except Exception:
                due_dt = None

        attach_url = None
        if f and getattr(f, "filename", "") and cloud_name and upload_preset:
            try:
                r = requests.post(
                    f"https://api.cloudinary.com/v1_1/{cloud_name}/auto/upload",
                    data={"upload_preset": upload_preset, "resource_type": "auto"},
                    files={"file": (f.filename, f.stream, f.mimetype)},
                    timeout=30,
                )
                if r.ok:
                    attach_url = r.json().get("secure_url") or r.json().get("url")
            except Exception:
                attach_url = None

        db.session.add(
            CollectionPayment(
                order_id=order_id,
                kind=kind,
                method=method,
                amount=amount_val,
                due_date=due_dt,
                attachment_url=attach_url,
                notes=row_note,
            )
        )

        if kind == "CREDIT_NOTE":
            total_credit += abs(amount_val)
        else:
            total_paid += abs(amount_val)

        if due_dt and (max_due is None or due_dt > max_due):
            max_due = due_dt

    # Update collection summary
    try:
        candidates = []
        if getattr(coll, "monto", None) is not None:
            candidates.append(float(coll.monto))
        if getattr(o, "precio_final", None) is not None:
            candidates.append(float(o.precio_final))
        lg = getattr(o, "logistics", None)
        if lg is not None and getattr(lg, "precio", None) is not None:
            candidates.append(float(lg.precio))
        picked = None
        for v in candidates:
            if v is not None and v > 0:
                picked = v
                break
        if picked is None and candidates:
            picked = candidates[0]
        base_monto = float(picked or 0.0)
    except Exception:
        base_monto = 0.0
    net_due = base_monto - total_credit
    # Marcar cobrado si cubre el neto
    if net_due <= 0:
        coll.fecha_cobro_efectiva = datetime.utcnow()
    elif total_paid >= net_due and net_due > 0:
        coll.fecha_cobro_efectiva = datetime.utcnow()
    # Vencimiento estimado: tomar el más lejano ingresado (p.ej. cheque)
    if max_due:
        coll.fecha_pago_estimada = max_due

    db.session.commit()
    return redirect(url_for("main.deudas_pendientes"))


@bp.post("/cobranzas/<int:order_id>/cobrar")
def cobranzas_mark_cobrado(order_id: int):
    coll = Collection.query.filter_by(order_id=order_id).first_or_404()
    coll.fecha_cobro_efectiva = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("main.deudas_pendientes"))


@bp.post("/cobranzas/<int:order_id>/update")
def cobranzas_update(order_id: int):
    coll = Collection.query.filter_by(order_id=order_id).first_or_404()
    monto = request.form.get("monto", type=float)
    forma_pago_raw = request.form.get("forma_pago")
    forma_pago = None if forma_pago_raw == "" else (PaymentMethod(forma_pago_raw) if forma_pago_raw else None)
    pago_estimado = request.form.get("pago_estimado")
    entrega_efectiva_raw = (request.form.get("fecha_entrega_efectiva") or "").strip()
    cobro_efectivo_raw = (request.form.get("fecha_cobro_efectiva") or "").strip()
    if monto is not None:
        coll.monto = monto
    if forma_pago_raw is not None:
        coll.forma_pago = forma_pago
    if pago_estimado is not None:
        coll.fecha_pago_estimada = datetime.fromisoformat(pago_estimado) if pago_estimado else None
    if entrega_efectiva_raw is not None:
        try:
            coll.fecha_entrega_efectiva = datetime.fromisoformat(entrega_efectiva_raw) if entrega_efectiva_raw else None
        except Exception:
            pass
    if cobro_efectivo_raw is not None:
        try:
            coll.fecha_cobro_efectiva = datetime.fromisoformat(cobro_efectivo_raw) if cobro_efectivo_raw else None
        except Exception:
            pass
    # Mantener consistencia con status si existe registro
    logistics = LogisticsStatus.query.filter_by(order_id=order_id).first()
    if logistics:
        if monto is not None:
            logistics.precio = monto
        if forma_pago_raw is not None:
            logistics.forma_pago = forma_pago
        if entrega_efectiva_raw is not None:
            try:
                logistics.fecha_entrega_efectiva = datetime.fromisoformat(entrega_efectiva_raw) if entrega_efectiva_raw else None
            except Exception:
                pass
    # Mantener consistencia con la Orden
    order = Order.query.get(order_id)
    if order:
        if monto is not None:
            order.precio_final = monto
        if forma_pago_raw is not None:
            order.forma_pago = forma_pago
    db.session.commit()
    return redirect(url_for("main.deudas_pendientes"))


@bp.get("/historial")
def historial():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template("historial.html", active="historial", orders=orders)

@bp.get("/crm")
def crm():
    rows = []
    clients = Client.query.order_by(Client.apellido, Client.nombre).all()
    # Mapeo de etiquetas
    label_map = {
        RelationStatus.TRABAJA.value: "Trabaja",
        RelationStatus.TRABAJABA.value: "Trabajaba",
        RelationStatus.A_INCORPORAR.value: "A Incorporar",
    }
    for c in clients:
        # Determinar todas las empresas relacionadas ya sea por órdenes o por vínculos
        company_ids = set()
        # Por órdenes
        for row in db.session.query(Order.company_id).filter(Order.client_id == c.id).distinct():
            if row[0]:
                company_ids.add(int(row[0]))
        # Por links
        for l in c.links:
            if l.company_id:
                company_ids.add(int(l.company_id))

        if not company_ids:
            # Sin empresas, mostrar fila resumida sin empresa
            rows.append({
                "cliente": f"{c.apellido} {c.nombre}",
                "empresa": "-",
                "ultima_compra": None,
                "cobradas": 0,
                "promedio": None,
                "categ": "-",
            })
            continue

        for cid in sorted(company_ids):
            comp = Company.query.get(cid)
            if not comp:
                continue
            # Última compra cliente-empresa
            last_order = (
                Order.query.filter_by(client_id=c.id, company_id=cid)
                .order_by(Order.created_at.desc())
                .first()
            )
            last_date = last_order.created_at if last_order else None
            # Compras cobradas cliente-empresa
            cobradas = (
                db.session.query(func.count(Collection.id))
                .join(Order, Collection.order_id == Order.id)
                .filter(Order.client_id == c.id, Order.company_id == cid, Collection.fecha_cobro_efectiva.isnot(None))
                .scalar()
            ) or 0
            # Compra promedio cliente-empresa
            avg_compra = (
                db.session.query(func.avg(Order.precio_final))
                .filter(Order.client_id == c.id, Order.company_id == cid)
                .scalar()
            )
            # Categorización por link específico si existe
            link = next((l for l in c.links if l.company_id == cid), None)
            st_val = getattr(getattr(link, "status", None), "value", None)
            categ = label_map.get(st_val, "-")

            rows.append({
                "cliente": f"{c.apellido} {c.nombre}",
                "empresa": comp.nombre,
                "ultima_compra": last_date,
                "cobradas": int(cobradas),
                "promedio": float(avg_compra) if avg_compra is not None else None,
                "categ": categ,
            })

    return render_template("crm.html", active="crm", items=rows)


@bp.post("/pedidos/<int:order_id>/edit")
def pedidos_edit(order_id: int):
    o = Order.query.get_or_404(order_id)
    o.nota = request.form.get("nota") or o.nota
    o.descripcion = request.form.get("descripcion") or o.descripcion
    precio_final = request.form.get("precio_final", type=float)
    if precio_final is not None:
        o.precio_final = precio_final
        # Sincronizar con logistics y collection
        if o.logistics:
            o.logistics.precio = precio_final
        if o.collection:
            o.collection.monto = precio_final
    forma_pago_raw = request.form.get("forma_pago")
    forma_pago = None if forma_pago_raw == "" else (PaymentMethod(forma_pago_raw) if forma_pago_raw else None)
    if forma_pago_raw is not None:
        o.forma_pago = forma_pago
        if o.logistics:
            o.logistics.forma_pago = forma_pago
        if o.collection:
            o.collection.forma_pago = forma_pago
    db.session.commit()
    return redirect(url_for("main.historial"))


@bp.post("/pedidos/<int:order_id>/delete")
def pedidos_delete(order_id: int):
    o = Order.query.get_or_404(order_id)
    db.session.delete(o)
    db.session.commit()
    return redirect(url_for("main.historial"))


@bp.get("/api/clientes/<int:client_id>/sucursales")
def api_client_branches(client_id: int):
    client = Client.query.get_or_404(client_id)
    rows = ClientBranch.query.filter_by(client_id=client_id).order_by(ClientBranch.nombre).all()
    # Compatibilidad: si no hay filas en ClientBranch pero el cliente tiene sucursal legacy,
    # crear una entrada básica para que aparezca en el combo de pedidos.
    if not rows and getattr(client, "sucursal", None):
        try:
            b = ClientBranch(client_id=client.id, nombre=client.sucursal)
            db.session.add(b)
            db.session.commit()
            rows = [b]
        except Exception:
            db.session.rollback()
    return jsonify([{"id": r.id, "label": r.nombre} for r in rows])


@bp.get("/api/pedidos/anteriores")
def api_prev_orders():
    client_id = request.args.get("client_id", type=int)
    company_id = request.args.get("company_id", type=int)
    limit = request.args.get("limit", default=10, type=int)
    if not client_id or not company_id:
        abort(400)
    q = (
        Order.query.filter_by(client_id=client_id, company_id=company_id)
        .order_by(Order.created_at.desc())
        .limit(max(1, min(limit, 50)))
    )
    items = []
    for o in q.all():
        items.append({
            "id": o.id,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "created_date": o.created_at.date().isoformat() if o.created_at else None,
            "nota": o.nota or "",
            "descripcion": o.descripcion or "",
            "precio_final": float(o.precio_final or 0),
            "forma_pago": getattr(o.forma_pago, "value", None),
        })
    return jsonify(items)


@bp.post("/clientes/<int:client_id>/sucursales")
def client_branch_add(client_id: int):
    Client.query.get_or_404(client_id)
    nombre = (request.form.get("nombre") or "").strip()
    if not nombre:
        abort(400)
    b = ClientBranch(client_id=client_id, nombre=nombre)
    db.session.add(b)
    db.session.commit()
    return redirect(url_for("main.clientes"))


@bp.post("/clientes/<int:client_id>/sucursales/<int:branch_id>/delete")
def client_branch_delete(client_id: int, branch_id: int):
    b = ClientBranch.query.filter_by(id=branch_id, client_id=client_id).first_or_404()
    db.session.delete(b)
    db.session.commit()
    return redirect(url_for("main.clientes"))
