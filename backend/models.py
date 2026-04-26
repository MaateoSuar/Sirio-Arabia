from datetime import datetime, timedelta, date
from enum import Enum
from .extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class AppUser(db.Model, UserMixin):
    __tablename__ = "app_user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    permissions_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password or "")

    def check_password(self, password: str) -> bool:
        try:
            return check_password_hash(self.password_hash or "", password or "")
        except Exception:
            return False


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
    plazo_pago_dias = db.Column(db.Integer)

    __table_args__ = (db.UniqueConstraint("client_id", "company_id", name="uq_client_company"),)


class ClientCompanyBalance(db.Model):
    __tablename__ = "client_company_balance"
    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    balance_adjustment = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("owner_user_id", "client_id", "company_id", name="uq_client_company_balance"),
    )


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
    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    archived = db.Column(db.Boolean, nullable=False, default=False)
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
    nota_pedido = db.Column(db.Text)
    plazo_usual = db.Column(db.String(120))
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


class CompanyProductSheet(db.Model):
    __tablename__ = "company_product_sheet"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500))  # URL (Cloudinary) opcional
    data = db.Column(db.LargeBinary)  # BLOB opcional (fallback)
    mimetype = db.Column(db.String(120))
    size = db.Column(db.Integer)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    company = db.relationship("Company", backref=db.backref("product_sheets", cascade="all, delete-orphan"))


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    deleted_at = db.Column(db.DateTime)

    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))

    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)

    sucursal = db.Column(db.String(120))
    branch_id = db.Column(db.Integer, db.ForeignKey("client_branch.id"))
    nota = db.Column(db.Text)
    descripcion = db.Column(db.Text)

    precio_final = db.Column(db.Numeric(12, 2))
    forma_pago = db.Column(db.Enum(PaymentMethod))
    forma_pago_detalle = db.Column(db.String(32))

    # Tipo de comprobante asociado al pedido (por ejemplo FACTURA o REMITO)
    tipo_comprobante = db.Column(db.String(16))

    # Plazo de pago específico del pedido (días). Si es None, usar el de la empresa.
    plazo_pago_dias = db.Column(db.Integer)

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
    forma_pago_detalle = db.Column(db.String(32))

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

    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))

    fecha_entrega_efectiva = db.Column(db.DateTime)
    monto = db.Column(db.Numeric(12, 2))
    forma_pago = db.Column(db.Enum(PaymentMethod))
    forma_pago_detalle = db.Column(db.String(32))

    fecha_pago_estimada = db.Column(db.DateTime)
    fecha_cobro_efectiva = db.Column(db.DateTime)

    @property
    def status(self):
        if self.fecha_cobro_efectiva:
            return "COBRADO"
        if self.fecha_pago_estimada and datetime.utcnow() > self.fecha_pago_estimada:
            return "ATRASADO"
        return "EN CAMINO"


class CollectionPayment(db.Model):
    __tablename__ = "collection_payment"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)

    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    kind = db.Column(db.String(16), nullable=False)  # PAYMENT / CREDIT_NOTE
    method = db.Column(db.String(32))
    amount = db.Column(db.Numeric(12, 2))
    due_date = db.Column(db.DateTime)
    attachment_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    voided_at = db.Column(db.DateTime)
    voided_by_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    voided_reason = db.Column(db.Text)

    order = db.relationship("Order", backref=db.backref("collection_payments", cascade="all, delete-orphan"))


class CommissionState(db.Model):
    __tablename__ = "commission_state"
    id = db.Column(db.Integer, primary_key=True)
    last_paid_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    branch_id = db.Column(db.Integer)
    sucursal = db.Column(db.String(120))
    nota = db.Column(db.Text)
    descripcion = db.Column(db.Text)
    precio_final = db.Column(db.Numeric(12, 2))
    forma_pago = db.Column(db.Enum(PaymentMethod))
    forma_pago_detalle = db.Column(db.String(32))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("client_id", "company_id", name="uq_draft_client_company"),)


class CollectionDraft(db.Model):
    __tablename__ = "collection_draft"
    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    notes = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ClientDocument(db.Model):
    __tablename__ = "client_document"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)

    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
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

    owner_user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
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
