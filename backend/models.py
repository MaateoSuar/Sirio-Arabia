from datetime import datetime, timedelta, date
from enum import Enum
from .extensions import db


class RelationStatus(str, Enum):
    TRABAJA = "TRABAJA"
    TRABAJABA = "TRABAJABA"
    A_INCORPORAR = "A_INCORPORAR"


class PaymentMethod(str, Enum):
    EFECTIVO = "EFECTIVO"
    CHEQUE = "CHEQUE"
    TRANSFERENCIA = "TRANSFERENCIA"
    NO_SE_SABE = "NO_SE_SABE"


class ClientCompanyLink(db.Model):
    __tablename__ = "client_company_link"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    status = db.Column(db.Enum(RelationStatus), nullable=False, default=RelationStatus.TRABAJA)
    comprobante_tipo = db.Column(db.String(20), nullable=False, default="FACTURA")
    descuento = db.Column(db.Numeric(5, 2))

    __table_args__ = (db.UniqueConstraint("client_id", "company_id", name="uq_client_company"),)


class ClientBranch(db.Model):
    __tablename__ = "client_branch"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    nombre = db.Column(db.String(120), nullable=False)

    __table_args__ = (db.UniqueConstraint("client_id", "nombre", name="uq_client_branch_name"),)


class ClientDeliveryPlace(db.Model):
    __tablename__ = "client_delivery_place"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    nombre = db.Column(db.String(255), nullable=False)

    # Datos específicos por lugar de entrega
    provincia = db.Column(db.String(80))
    nota = db.Column(db.String(255))
    horario = db.Column(db.String(255))
    contacto = db.Column(db.String(255))
    telefono = db.Column(db.String(64))

    __table_args__ = (db.UniqueConstraint("client_id", "nombre", name="uq_client_delivery_name"),)


class ClientBirthday(db.Model):
    __tablename__ = "client_birthday"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    nombre = db.Column(db.String(255), nullable=False)
    puesto = db.Column(db.String(255))
    fecha = db.Column(db.Date)
    notas = db.Column(db.Text)

    __table_args__ = (db.UniqueConstraint("client_id", "nombre", "puesto", name="uq_client_birthday"),)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    apellido = db.Column(db.String(120), nullable=False)
    nombre = db.Column(db.String(120), nullable=False)
    sucursal = db.Column(db.String(120))
    cuit = db.Column(db.String(32))
    direccion_principal = db.Column(db.String(255))
    forma_pago_habitual = db.Column(db.String(32))
    transporte_recomendado = db.Column(db.String(120))
    delivery_schedule = db.Column(db.String(255))
    delivery_contact = db.Column(db.String(255))
    delivery_phone = db.Column(db.String(64))
    provincia = db.Column(db.String(80))
    fecha_incorporacion = db.Column(db.Date, default=date.today)
    telefono = db.Column(db.String(50))
    mail = db.Column(db.String(255))
    transporte_contacto = db.Column(db.String(255))

    links = db.relationship("ClientCompanyLink", backref="client", cascade="all, delete-orphan")
    orders = db.relationship("Order", backref="client", cascade="all, delete-orphan")
    branches = db.relationship("ClientBranch", backref="client", cascade="all, delete-orphan")
    delivery_places = db.relationship("ClientDeliveryPlace", backref="client", cascade="all, delete-orphan")
    birthdays = db.relationship("ClientBirthday", backref="client", cascade="all, delete-orphan")
    documents = db.relationship("ClientDocument", backref="client", cascade="all, delete-orphan")

    @property
    def empresas_trabaja(self):
        return [l for l in self.links if l.status == RelationStatus.TRABAJA]

    @property
    def empresas_trabajaba(self):
        return [l for l in self.links if l.status == RelationStatus.TRABAJABA]

    @property
    def empresas_a_incorporar(self):
        return [l for l in self.links if l.status == RelationStatus.A_INCORPORAR]


class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(200), nullable=False, unique=True)
    marca = db.Column(db.String(120))
    demora_despacho_promedio_dias = db.Column(db.Integer, default=0)
    plazo_pago_promedio_dias = db.Column(db.Integer, default=30)
    mail_pedido = db.Column(db.String(255))
    mail_pago = db.Column(db.String(255))
    pedido_estandar_recomendado = db.Column(db.Text)
    plazo_usual = db.Column(db.String(120))
    tipo_comprobante_default = db.Column(db.String(16))
    forma_pago_default = db.Column(db.String(32))
    # Información complementaria
    cuit = db.Column(db.String(32))
    notas = db.Column(db.Text)
    cuenta_bancaria_notas = db.Column(db.Text)

    links = db.relationship("ClientCompanyLink", backref="company", cascade="all, delete-orphan")
    orders = db.relationship("Order", backref="company", cascade="all, delete-orphan")


class CompanyDocument(db.Model):
    __tablename__ = "company_document"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    # category: por ejemplo CONSTANCIA, CATALOGO
    category = db.Column(db.String(32), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    data = db.Column(db.LargeBinary)
    mimetype = db.Column(db.String(120))
    size = db.Column(db.Integer)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    company = db.relationship("Company", backref=db.backref("documents", cascade="all, delete-orphan"))


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)

    sucursal = db.Column(db.String(120))
    branch_id = db.Column(db.Integer, db.ForeignKey("client_branch.id"))
    nota = db.Column(db.Text)
    descripcion = db.Column(db.Text)

    precio_final = db.Column(db.Numeric(12, 2))
    forma_pago = db.Column(db.Enum(PaymentMethod))

    # Tipo de comprobante asociado al pedido (por ejemplo FACTURA o REMITO)
    tipo_comprobante = db.Column(db.String(16))

    # Snapshots from company at order time
    demora_despacho_promedio_dias = db.Column(db.Integer)
    mail_pedido = db.Column(db.String(255))

    logistics = db.relationship("LogisticsStatus", uselist=False, backref="order", cascade="all, delete-orphan")
    collection = db.relationship("Collection", uselist=False, backref="order", cascade="all, delete-orphan")


class LogisticsStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False, unique=True)

    fecha_compra = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_entrega_estimada = db.Column(db.DateTime)
    fecha_entrega_efectiva = db.Column(db.DateTime)

    precio = db.Column(db.Numeric(12, 2))
    forma_pago = db.Column(db.Enum(PaymentMethod))

    nota = db.Column(db.Text)
    descripcion = db.Column(db.Text)

    @property
    def status(self):
        if self.fecha_entrega_efectiva:
            return "ENTREGADO"
        if self.fecha_entrega_estimada and datetime.utcnow() > self.fecha_entrega_estimada:
            return "ATRASADO"
        return "EN CAMINO"


class Collection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False, unique=True)

    fecha_entrega_efectiva = db.Column(db.DateTime)
    monto = db.Column(db.Numeric(12, 2))
    forma_pago = db.Column(db.Enum(PaymentMethod))

    fecha_pago_estimada = db.Column(db.DateTime)
    fecha_cobro_efectiva = db.Column(db.DateTime)

    @property
    def status(self):
        if self.fecha_cobro_efectiva:
            return "COBRADO"
        if self.fecha_pago_estimada and datetime.utcnow() > self.fecha_pago_estimada:
            return "ATRASADO"
        return "EN CAMINO"


class OrderAttachment(db.Model):
    __tablename__ = "order_attachment"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    order = db.relationship("Order", backref=db.backref("attachments", cascade="all, delete-orphan"))


class OrderDraft(db.Model):
    __tablename__ = "order_draft"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    branch_id = db.Column(db.Integer)
    sucursal = db.Column(db.String(120))
    nota = db.Column(db.Text)
    descripcion = db.Column(db.Text)
    precio_final = db.Column(db.Numeric(12, 2))
    forma_pago = db.Column(db.Enum(PaymentMethod))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("client_id", "company_id", name="uq_draft_client_company"),)


class ClientDocument(db.Model):
    __tablename__ = "client_document"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    category = db.Column(db.String(32))
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    # Nuevo: soporte de almacenamiento en DB (Postgres) para archivos
    data = db.Column(db.LargeBinary)
    mimetype = db.Column(db.String(120))
    size = db.Column(db.Integer)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class ClientAlertState(db.Model):
    __tablename__ = "client_alert_state"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    dismissed_at = db.Column(db.DateTime)
    snoozed_until = db.Column(db.DateTime)
    message = db.Column(db.Text)
    severity = db.Column(db.String(16))
    company = db.Column(db.String(200))
    first_seen_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("client_id", "order_id", "kind", name="uq_client_alert_state"),
    )
