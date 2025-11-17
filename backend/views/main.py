from flask import Blueprint, render_template, request, redirect, url_for, jsonify, abort
from datetime import date, datetime, timedelta
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None
import os
import requests
from ..extensions import db
from ..models import Client, Company, ClientCompanyLink, RelationStatus, Order, LogisticsStatus, Collection, PaymentMethod, ClientBranch, OrderAttachment
from sqlalchemy import func

bp = Blueprint("main", __name__)


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
    return render_template("clientes.html", active="clientes", items=items, companies=companies)


@bp.get("/clientes/nuevo")
def clientes_new():
    companies = Company.query.order_by(Company.nombre).all()
    branches = [r[0] for r in db.session.query(Client.sucursal).filter(Client.sucursal.isnot(None)).distinct().order_by(Client.sucursal).all()]
    return render_template("clientes_form.html", active="clientes", companies=companies, branches=branches)


@bp.post("/clientes/nuevo")
def clientes_create():
    apellido = request.form.get("apellido", "").strip()
    nombre = request.form.get("nombre", "").strip()
    # sucursales múltiples
    branch_list = [b.strip() for b in request.form.getlist("branch_list") if (b or "").strip()]
    telefono = request.form.get("telefono", "").strip()
    mails = [m.strip() for m in request.form.getlist("mails") if (m or "").strip()]
    mail_single = (request.form.get("mail", "") or "").strip()
    # store as comma-separated for backward compatibility
    mail = ", ".join(mails) if mails else (mail_single or None)
    relacion = (request.form.get("relacion") or "").strip() or None
    fecha_inc = request.form.get("fecha_incorporacion") or None
    company_id = request.form.get("company_id", type=int)
    # Guardar compat 'sucursal' como la primera si viene lista
    compat_sucursal = (branch_list[0] if branch_list else None)
    client = Client(apellido=apellido or "-", nombre=nombre or "-", sucursal=compat_sucursal,
                    telefono=telefono or None, mail=mail or None,
                    fecha_incorporacion=date.fromisoformat(fecha_inc) if fecha_inc else None)
    db.session.add(client)
    db.session.commit()
    # Crear sucursales
    if branch_list:
        for nm in branch_list:
            try:
                db.session.add(ClientBranch(client_id=client.id, nombre=nm))
            except Exception:
                pass
        db.session.commit()
    # Si se indicó relación y empresa, crear/actualizar vínculo; si no hay empresa, aplicar a vínculos existentes
    if relacion and company_id:
        try:
            st = RelationStatus(relacion)
            comp = Company.query.get(company_id)
            if comp:
                link = ClientCompanyLink.query.filter_by(client_id=client.id, company_id=comp.id).first()
                if not link:
                    link = ClientCompanyLink(client_id=client.id, company_id=comp.id, status=st)
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


# Gestión de vínculos desde Clientes (bilateral con Empresas)
@bp.post("/clientes/<int:client_id>/links/add")
def clientes_link_add(client_id: int):
    client = Client.query.get_or_404(client_id)
    company_id = request.form.get("company_id", type=int)
    status = request.form.get("status") or RelationStatus.TRABAJA.value
    comp = Company.query.get_or_404(company_id)
    link = ClientCompanyLink.query.filter_by(client_id=client.id, company_id=comp.id).first()
    if not link:
        link = ClientCompanyLink(client_id=client.id, company_id=comp.id)
        db.session.add(link)
    link.status = RelationStatus(status)
    db.session.commit()
    return redirect(url_for("main.clientes"))


@bp.post("/clientes/<int:client_id>/links/<int:link_id>/status")
def clientes_link_update(client_id: int, link_id: int):
    link = ClientCompanyLink.query.filter_by(id=link_id, client_id=client_id).first_or_404()
    status = request.form.get("status") or None
    if status:
        link.status = RelationStatus(status)
        db.session.commit()
    return redirect(url_for("main.clientes"))


@bp.post("/clientes/<int:client_id>/links/<int:link_id>/delete")
def clientes_link_delete(client_id: int, link_id: int):
    link = ClientCompanyLink.query.filter_by(id=link_id, client_id=client_id).first_or_404()
    db.session.delete(link)
    db.session.commit()
    return redirect(url_for("main.clientes"))


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
    # sucursales múltiples
    branch_list = [b.strip() for b in request.form.getlist("branch_list") if (b or "").strip()]
    # compat: actualizar sucursal como primera de la lista
    obj.sucursal = (branch_list[0] if branch_list else None)
    obj.telefono = request.form.get("telefono") or None
    mails = [m.strip() for m in request.form.getlist("mails") if (m or "").strip()]
    mail_single = (request.form.get("mail") or "").strip()
    obj.mail = (", ".join(mails) if mails else (mail_single or None))
    relacion = (request.form.get("relacion") or "").strip() or None
    fecha_inc = request.form.get("fecha_incorporacion") or None
    obj.fecha_incorporacion = date.fromisoformat(fecha_inc) if fecha_inc else obj.fecha_incorporacion
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
                "status": getattr(l.status, "value", str(l.status))
            })
    # Sort by name
    rows.sort(key=lambda r: r["label"].lower())
    return jsonify(rows)


@bp.get("/empresas")
def empresas():
    items = Company.query.order_by(Company.nombre).all()
    clients = Client.query.order_by(Client.apellido, Client.nombre).all()
    return render_template("empresas.html", active="empresas", items=items, clients=clients)


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
    mail_pedido_list = [m.strip() for m in request.form.getlist("mail_pedido_list") if (m or "").strip()]
    mail_pedido_single = (request.form.get("mail_pedido", "") or "").strip()
    mail_pedido = ", ".join(mail_pedido_list) if mail_pedido_list else (mail_pedido_single or None)
    mail_pago = request.form.get("mail_pago", "").strip() or None
    company = Company(nombre=nombre or "-", marca=marca, demora_despacho_promedio_dias=demora or 0,
                      plazo_pago_promedio_dias=plazo or 30, mail_pedido=mail_pedido, mail_pago=mail_pago)
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
                link = ClientCompanyLink(client_id=cid, company_id=company.id, status=RelationStatus.TRABAJA)
                db.session.add(link)
    db.session.commit()
    return redirect(url_for("main.empresas"))


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
    mail_pedido_list = [m.strip() for m in request.form.getlist("mail_pedido_list") if (m or "").strip()]
    mail_pedido_single = (request.form.get("mail_pedido") or "").strip()
    obj.mail_pedido = (", ".join(mail_pedido_list) if mail_pedido_list else (mail_pedido_single or None))
    obj.mail_pago = (request.form.get("mail_pago") or None)
    db.session.commit()
    return redirect(url_for("main.empresas"))

@bp.post("/empresas/<int:company_id>/links/add")
def empresas_link_add(company_id: int):
    company = Company.query.get_or_404(company_id)
    client_id = request.form.get("client_id", type=int)
    status = request.form.get("status") or RelationStatus.TRABAJA.value
    client = Client.query.get_or_404(client_id)
    link = ClientCompanyLink.query.filter_by(client_id=client.id, company_id=company.id).first()
    if not link:
        link = ClientCompanyLink(client_id=client.id, company_id=company.id)
        db.session.add(link)
    link.status = RelationStatus(status)
    db.session.commit()
    return redirect(url_for("main.empresas"))


@bp.post("/empresas/<int:company_id>/links/<int:link_id>/status")
def empresas_link_update(company_id: int, link_id: int):
    link = ClientCompanyLink.query.filter_by(id=link_id, company_id=company_id).first_or_404()
    status = request.form.get("status") or None
    if status:
        link.status = RelationStatus(status)
        db.session.commit()
    return redirect(url_for("main.empresas"))


@bp.post("/empresas/<int:company_id>/links/<int:link_id>/delete")
def empresas_link_delete(company_id: int, link_id: int):
    link = ClientCompanyLink.query.filter_by(id=link_id, company_id=company_id).first_or_404()
    db.session.delete(link)
    db.session.commit()
    return redirect(url_for("main.empresas"))


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
                           clientes=Client.query.order_by(Client.apellido, Client.nombre).all(),
                           empresas=Company.query.order_by(Company.nombre).all())


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
    precio_final = request.form.get("precio_final", type=float)
    forma_pago = request.form.get("forma_pago") or None

    if not client_id:
        abort(400, "Debe seleccionar un cliente")
    if not company_id:
        abort(400, "Debe seleccionar una empresa vinculada al cliente")
    if not forma_pago:
        abort(400, "La forma de pago es obligatoria")

    client = Client.query.get_or_404(client_id)
    company = Company.query.get_or_404(company_id)

    # Si no se envió branch_id, tomar la primera sucursal del cliente como default
    if not branch_id:
        first_branch = ClientBranch.query.filter_by(client_id=client.id).order_by(ClientBranch.id.asc()).first()
        if first_branch:
            branch_id = first_branch.id
            # también setear texto sucursal si no vino
            if not sucursal:
                sucursal = first_branch.nombre

    order = Order(client=client, company=company, sucursal=sucursal, branch_id=branch_id, nota=nota, descripcion=descripcion,
                  precio_final=precio_final, forma_pago=PaymentMethod(forma_pago),
                  demora_despacho_promedio_dias=company.demora_despacho_promedio_dias,
                  mail_pedido=company.mail_pedido)
    db.session.add(order)
    db.session.flush()

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
                r = requests.post(f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload", data={"upload_preset": upload_preset}, files={"file": (f.filename, f.stream, f.mimetype)})
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
        coll.fecha_pago_estimada = logistics.fecha_entrega_efectiva + timedelta(days=30)
        coll.monto = logistics.precio
        coll.forma_pago = logistics.forma_pago
        db.session.commit()
    return redirect(url_for("main.cobranzas"))


@bp.post("/status/<int:order_id>/update")
def status_update(order_id: int):
    logistics = LogisticsStatus.query.filter_by(order_id=order_id).first_or_404()
    precio = request.form.get("precio", type=float)
    forma_pago_raw = request.form.get("forma_pago")
    forma_pago = None if forma_pago_raw == "" else (PaymentMethod(forma_pago_raw) if forma_pago_raw else None)
    fecha_entrega_estimada_raw = (request.form.get("fecha_entrega_estimada") or "").strip()
    if precio is not None:
        logistics.precio = precio
    if forma_pago_raw is not None:
        logistics.forma_pago = forma_pago
    if fecha_entrega_estimada_raw:
        try:
            logistics.fecha_entrega_estimada = datetime.fromisoformat(fecha_entrega_estimada_raw)
        except Exception:
            pass
    # Mantener consistencia con cobranzas si existe registro
    coll = Collection.query.filter_by(order_id=order_id).first()
    if coll:
        if precio is not None:
            coll.monto = precio
        if forma_pago_raw is not None:
            coll.forma_pago = forma_pago
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
    q = Collection.query.join(Order)
    estados = request.args.getlist("status")
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
    return render_template("cobranzas.html", active="cobranzas", items=items)


@bp.post("/cobranzas/<int:order_id>/cobrar")
def cobranzas_mark_cobrado(order_id: int):
    coll = Collection.query.filter_by(order_id=order_id).first_or_404()
    coll.fecha_cobro_efectiva = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("main.cobranzas"))


@bp.post("/cobranzas/<int:order_id>/update")
def cobranzas_update(order_id: int):
    coll = Collection.query.filter_by(order_id=order_id).first_or_404()
    monto = request.form.get("monto", type=float)
    forma_pago_raw = request.form.get("forma_pago")
    forma_pago = None if forma_pago_raw == "" else (PaymentMethod(forma_pago_raw) if forma_pago_raw else None)
    pago_estimado = request.form.get("pago_estimado")
    if monto is not None:
        coll.monto = monto
    if forma_pago_raw is not None:
        coll.forma_pago = forma_pago
    if pago_estimado is not None:
        coll.fecha_pago_estimada = datetime.fromisoformat(pago_estimado) if pago_estimado else None
    # Mantener consistencia con status si existe registro
    logistics = LogisticsStatus.query.filter_by(order_id=order_id).first()
    if logistics:
        if monto is not None:
            logistics.precio = monto
        if forma_pago_raw is not None:
            logistics.forma_pago = forma_pago
    # Mantener consistencia con la Orden
    order = Order.query.get(order_id)
    if order:
        if monto is not None:
            order.precio_final = monto
        if forma_pago_raw is not None:
            order.forma_pago = forma_pago
    db.session.commit()
    return redirect(url_for("main.cobranzas"))


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
    Client.query.get_or_404(client_id)
    rows = ClientBranch.query.filter_by(client_id=client_id).order_by(ClientBranch.nombre).all()
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
