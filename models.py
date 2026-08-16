from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask_login import UserMixin
from flask_bcrypt import generate_password_hash, check_password_hash

db = SQLAlchemy()

class Section(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(200))
    icon = db.Column(db.String(50))
    items = db.relationship('Item', backref='section', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return self.name

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey('section.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    field_type = db.Column(db.String(20), default='text')
    unit = db.Column(db.String(20))
    default_value = db.Column(db.String(50))
    options = db.Column(db.Text)
    is_kpi = db.Column(db.Boolean, default=False)
    weight = db.Column(db.Float, default=1.0)
    impact_type = db.Column(db.String(10), default='positive')
    records = db.relationship('Record', backref='item', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return self.title

class Record(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=False)
    shift_date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date)
    shift_name = db.Column(db.String(20))
    operator_id = db.Column(db.Integer, db.ForeignKey('operator.id'), nullable=True)
    value = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Supervisor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True)
    hire_date = db.Column(db.Date)
    email = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    operators = db.relationship('Operator', backref='supervisor', lazy=True)

    def __repr__(self):
        return self.name

class Operator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True)
    hire_date = db.Column(db.Date)
    skill_level = db.Column(db.String(20))
    shift_preference = db.Column(db.String(20))
    supervisor_id = db.Column(db.Integer, db.ForeignKey('supervisor.id'), nullable=True)
    records = db.relationship('Record', backref='operator', lazy=True)
    stops = db.relationship('StopRecord', backref='operator', lazy=True)

    def __repr__(self):
        return self.name

class StopRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shift_date = db.Column(db.Date, nullable=False)
    shift_name = db.Column(db.String(20))
    machine_name = db.Column(db.String(50))
    stop_duration = db.Column(db.Integer)
    root_cause = db.Column(db.String(100))
    description = db.Column(db.Text)
    operator_id = db.Column(db.Integer, db.ForeignKey('operator.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Responsibility(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    person_type = db.Column(db.String(20), nullable=False)
    person_id = db.Column(db.Integer, nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey('section.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    section = db.relationship('Section', backref='responsibilities')

    __table_args__ = (
        db.UniqueConstraint('person_type', 'person_id', 'section_id', name='unique_person_section'),
    )

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), default='operator')
    full_name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return self.username