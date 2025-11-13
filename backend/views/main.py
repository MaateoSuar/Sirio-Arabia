from flask import Blueprint, render_template, request, redirect, url_for, jsonify, abort
from datetime import date, datetime, timedelta
import os
import requests
from ..extensions import db
from ..models import Client, Company, ClientCompanyLink, RelationStatus, Order, LogisticsStatus, Collection, PaymentMethod, ClientBranch, OrderAttachment

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template("index.html", active="dashboard")


@bp.route("/clientes", methods=["GET", "POST"])
def clientes():
    if request.method == "POST":
        apellido = request.form.get("apellido", "").strip()
        nombre = request.form.get("nombre", "").strip()
        sucursal = request.form.get("sucursal", "").strip()
        telefono = request.form.get("telefono", "").strip()
        mail = request.form.get("mail", "").strip()
        fecha_inc = request.form.get("fecha_incorporacion") or None
        client = Client(apellido=apellido or "-", nombre=nombre or "-", sucursal=sucursal or None,
                        telefono=telefono or None, mail=mail or None,
                        fecha_incorporacion=date.fromisoformat(fecha_inc) if fecha_inc else None)
        db.session.add(client)
        db.session.commit()
        return redirect(url_for("main.clientes"))
    items = Client.query.order_by(Client.apellido, Client.nombre).all()
    return render_template("clientes.html", active="clientes", items=items)


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


@bp.route("/empresas", methods=["GET", "POST"])
def empresas():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        demora = request.form.get("demora", type=int)
        plazo = request.form.get("plazo", type=int)
        mail_pedido = request.form.get("mail_pedido", "").strip() or None
        mail_pago = request.form.get("mail_pago", "").strip() or None
        company = Company(nombre=nombre or "-", demora_despacho_promedio_dias=demora or 0,
                          plazo_pago_promedio_dias=plazo or 30, mail_pedido=mail_pedido, mail_pago=mail_pago)
        db.session.add(company)
        db.session.commit()
        return redirect(url_for("main.empresas"))
    items = Company.query.order_by(Company.nombre).all()
    clients = Client.query.order_by(Client.apellido, Client.nombre).all()
    return render_template("empresas.html", active="empresas", items=items, clients=clients)

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
    precio_final = request.form.get("precio_final", type=float)
    forma_pago = request.form.get("forma_pago") or None

    if not forma_pago:
        abort(400, "La forma de pago es obligatoria")

    client = Client.query.get_or_404(client_id)
    company = Company.query.get_or_404(company_id)

    order = Order(client=client, company=company, sucursal=sucursal, branch_id=branch_id, nota=nota, descripcion=descripcion,
                  precio_final=precio_final, forma_pago=PaymentMethod(forma_pago),
                  demora_despacho_promedio_dias=company.demora_despacho_promedio_dias,
                  mail_pedido=company.mail_pedido)
    db.session.add(order)
    db.session.flush()

    # Create logistics record
    fecha_compra = datetime.utcnow()
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
    items = q.order_by(LogisticsStatus.fecha_compra.desc()).all()
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
    forma_pago = request.form.get("forma_pago") or None
    if precio is not None:
        logistics.precio = precio
    if forma_pago:
        logistics.forma_pago = PaymentMethod(forma_pago)
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
    items = q.order_by(Collection.fecha_pago_estimada.desc()).all()
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
    forma_pago = request.form.get("forma_pago") or None
    pago_estimado = request.form.get("pago_estimado")
    if monto is not None:
        coll.monto = monto
    if forma_pago:
        coll.forma_pago = PaymentMethod(forma_pago)
    # Permitir limpiar la fecha si viene vacío
    if pago_estimado is not None:
        coll.fecha_pago_estimada = datetime.fromisoformat(pago_estimado) if pago_estimado else None
    db.session.commit()
    return redirect(url_for("main.cobranzas"))


@bp.get("/historial")
def historial():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template("historial.html", active="historial", orders=orders)


@bp.get("/crm")
def crm():
    return render_template("crm.html", active="crm")


@bp.post("/pedidos/<int:order_id>/edit")
def pedidos_edit(order_id: int):
    o = Order.query.get_or_404(order_id)
    o.nota = request.form.get("nota") or o.nota
    o.descripcion = request.form.get("descripcion") or o.descripcion
    precio_final = request.form.get("precio_final", type=float)
    if precio_final is not None:
        o.precio_final = precio_final
    forma_pago = request.form.get("forma_pago") or None
    if forma_pago:
        o.forma_pago = PaymentMethod(forma_pago)
        if o.logistics:
            o.logistics.forma_pago = o.forma_pago
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
