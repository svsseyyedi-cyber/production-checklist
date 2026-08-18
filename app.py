from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, Section, Item, Record, Operator, Supervisor, StopRecord, Responsibility, User, AlertLog
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from datetime import datetime, date, timedelta
import json
import random
import jdatetime
from functools import wraps
import math
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

import os  # اگر این خط از قبل در بالای فایل وجود ندارد، آن را اضافه کنید

# ========== تنظیمات دیتابیس ==========
if os.environ.get('DATABASE_URL'):
    # اگر در محیط تولید (Render) هستیم و DATABASE_URL تنظیم شده
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
else:
    # در محیط محلی از SQLite استفاده کن
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///checklist.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'fallback-secret-key')

app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['ADMIN_EMAIL'] = os.environ.get('ADMIN_EMAIL', 'admin@example.com')
app.config['MAIL_DEFAULT_SENDER'] = app.config['MAIL_USERNAME']

db.init_app(app)
bcrypt = Bcrypt(app)
mail = Mail(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'لطفاً ابتدا وارد شوید.'
login_manager.login_message_category = 'warning'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ===== توابع کمکی =====

def calculate_item_score(item, value):
    try:
        val = float(value)
    except (ValueError, TypeError):
        return 0.0

    target = item.target_value if item.target_value is not None else 100.0
    tolerance = item.tolerance if item.tolerance is not None else 10.0

    if item.impact_type == 'positive':
        diff = abs(val - target)
        max_diff = target * (tolerance / 100.0) if target != 0 else tolerance
        if max_diff == 0:
            raw_score = 100.0 if diff == 0 else 0.0
        else:
            raw_score = max(0.0, 100.0 * (1 - min(diff / max_diff, 1.0)))
    else:
        if target is None or target == 0:
            max_diff = tolerance if tolerance else 100
            raw_score = max(0.0, 100.0 * (1 - min(val / max_diff, 1.0)))
        else:
            diff = abs(val - target)
            max_diff = target * (tolerance / 100.0) if target != 0 else tolerance
            raw_score = max(0.0, 100.0 * (1 - min(diff / max_diff, 1.0)))

    if item.scoring_method == 'sigmoid':
        score = 100.0 / (1 + math.exp(-0.1 * (raw_score - 50)))
    elif item.scoring_method == 'step':
        if raw_score < 60:
            score = 0.0
        elif raw_score < 80:
            score = 50.0
        else:
            score = 100.0
    else:
        score = raw_score

    return round(score, 2)

def calculate_oee(last_date):
    total_planned_time = 480
    stops = 0
    stop_records = StopRecord.query.filter_by(shift_date=last_date).all()
    for s in stop_records:
        stops += s.stop_duration or 0
    availability = max(0, (total_planned_time - stops) / total_planned_time) if total_planned_time > 0 else 0

    actual_production = 0
    ideal_production = 0
    records = Record.query.filter_by(shift_date=last_date).all()
    for rec in records:
        if 'تولید واقعی' in rec.item.title:
            try: actual_production = float(rec.value)
            except: pass
        if 'تولید برنامه' in rec.item.title:
            try: ideal_production = float(rec.value)
            except: pass
    performance = min(1.0, (actual_production / ideal_production)) if ideal_production > 0 else 0

    waste = 0
    for rec in records:
        if 'کل ضایعات (ریال)' in rec.item.title:
            try: waste = float(rec.value)
            except: pass
    total_good = max(0, actual_production - (waste / 1000))
    quality = total_good / actual_production if actual_production > 0 else 0

    oee = availability * performance * quality * 100
    return round(min(oee, 100), 2)

def calculate_person_performance(person, sections, person_type):
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not last_date:
        return {
            'person': {
                'id': person.id,
                'name': person.name,
                'code': getattr(person, 'code', ''),
                'supervisor_name': getattr(person.supervisor, 'name', None) if hasattr(person, 'supervisor') else None
            },
            'type': person_type,
            'score': 0,
            'sections_count': len(sections),
            'category_scores': {},
            'efficiency': 0,
            'stops': 0,
            'waste': 0,
            'details': [],
            'operator_scores': []
        }

    section_scores = []
    category_aggregator = {}
    total_efficiency = 0
    total_stops = 0
    total_waste = 0
    count_eff = 0

    for section in sections:
        items = Item.query.filter_by(section_id=section.id).all()
        if not items:
            continue

        item_scores = []
        for item in items:
            rec = Record.query.filter_by(item_id=item.id, shift_date=last_date).first()
            if rec:
                score = calculate_item_score(item, rec.value)
                item_scores.append(score)
                cat = item.category or 'general'
                category_aggregator.setdefault(cat, []).append(score)

                if 'راندمان' in item.title:
                    try:
                        total_efficiency += float(rec.value)
                        count_eff += 1
                    except:
                        pass
                if 'مجموع توقف روزانه' in item.title:
                    try:
                        total_stops += float(rec.value)
                    except:
                        pass
                if 'کل ضایعات (ریال)' in item.title:
                    try:
                        total_waste += float(rec.value)
                    except:
                        pass

        if item_scores:
            weighted_sum = sum(s * (item.weight or 1.0) for s, item in zip(item_scores, items))
            total_weight = sum(item.weight or 1.0 for item in items)
            section_score = weighted_sum / total_weight if total_weight else 0
            section_scores.append(section_score * (section.weight or 1.0))

    total_section_weight = sum(sec.weight or 1.0 for sec in sections)
    personal_score = sum(section_scores) / total_section_weight if total_section_weight else 0

    avg_eff = total_efficiency / count_eff if count_eff else 0
    category_scores = {cat: round(sum(scores)/len(scores), 2) for cat, scores in category_aggregator.items() if scores}

    operator_scores = []
    if person_type == 'supervisor':
        operators = Operator.query.filter_by(supervisor_id=person.id).all()
        for op in operators:
            op_sections = [r.section for r in Responsibility.query.filter_by(person_type='operator', person_id=op.id).all()]
            if op_sections:
                op_perf = calculate_person_performance(op, op_sections, 'operator')
                operator_scores.append({
                    'operator_name': op.name,
                    'score': op_perf['score'],
                    'sections_count': len(op_sections)
                })

    if person_type == 'supervisor' and operator_scores:
        avg_operator_score = sum(op['score'] for op in operator_scores) / len(operator_scores)
        final_score = (personal_score * 0.5) + (avg_operator_score * 0.5)
    else:
        final_score = personal_score

    # تبدیل person به دیکشنری قابل سریال‌سازی
    person_dict = {
        'id': person.id,
        'name': person.name,
        'code': getattr(person, 'code', ''),
        'supervisor_name': person.supervisor.name if hasattr(person, 'supervisor') and person.supervisor else None
    }
    # اگر نوع اپراتور باشد، تعداد اپراتورهای زیردست را هم اضافه کنیم (برای گزارش‌ها)
    if person_type == 'supervisor':
        person_dict['operators_count'] = len(operators) if 'operators' in locals() else 0

    return {
        'person': person_dict,
        'type': person_type,
        'score': round(final_score, 2),
        'sections_count': len(sections),
        'category_scores': category_scores,
        'efficiency': round(avg_eff, 2),
        'stops': round(total_stops, 2),
        'waste': round(total_waste, 2),
        'details': [],
        'operator_scores': operator_scores
    }

def get_value_by_tag(tag, shift_date=None):
    """دریافت مقدار یک آیتم بر اساس تگ در تاریخ مشخص"""
    if not shift_date:
        shift_date = db.session.query(db.func.max(Record.shift_date)).scalar()
        if not shift_date:
            return None
    
    # جستجوی آیتم‌هایی که این تگ را دارند
    items = Item.query.filter(Item.tags.contains(tag)).all()
    if not items:
        return None
    
    # گرفتن اولین آیتم (یا می‌توان میانگین گرفت)
    item = items[0]
    record = Record.query.filter_by(item_id=item.id, shift_date=shift_date).first()
    if record:
        try:
            return float(record.value)
        except:
            return record.value
    return None

def get_all_values_by_tag(tag):
    """دریافت تمام مقادیر یک تگ در تاریخ‌های مختلف"""
    items = Item.query.filter(Item.tags.contains(tag)).all()
    if not items:
        return []
    
    item_ids = [i.id for i in items]
    records = Record.query.filter(Record.item_id.in_(item_ids)).order_by(Record.shift_date).all()
    
    result = []
    for rec in records:
        try:
            value = float(rec.value)
        except:
            value = rec.value
        result.append({
            'date': rec.shift_date,
            'value': value,
            'item_title': rec.item.title
        })
    return result



def calculate_overall_performance(sections):
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not last_date:
        return {'efficiency': 0, 'stops': 0, 'waste': 0, 'score': 0, 'details': []}

    total_efficiency = 0
    total_stops = 0
    total_waste = 0
    count = 0
    details = []

    for section in sections:
        items = Item.query.filter_by(section_id=section.id).all()
        for item in items:
            rec = Record.query.filter_by(item_id=item.id, shift_date=last_date).first()
            if rec:
                try:
                    value = float(rec.value)
                    score = calculate_item_score(item, rec.value)
                    details.append({
                        'item_title': item.title,
                        'value': value,
                        'weight': item.weight or 1.0,
                        'impact': item.impact_type,
                        'item_score': score
                    })
                    if 'راندمان' in item.title:
                        total_efficiency += value
                        count += 1
                    elif 'مجموع توقف روزانه' in item.title:
                        total_stops += value
                    elif 'کل ضایعات (ریال)' in item.title:
                        total_waste += value
                except:
                    pass

    avg_eff = total_efficiency / count if count > 0 else 0
    score = avg_eff - (total_stops * 0.5) - (total_waste / 1000)

    return {
        'efficiency': round(avg_eff, 2),
        'stops': round(total_stops, 2),
        'waste': round(total_waste, 2),
        'score': round(score, 2),
        'details': details
    }

def send_alert(alert_type, message, severity='info'):
    alert = AlertLog(alert_type=alert_type, message=message, severity=severity)
    db.session.add(alert)
    db.session.commit()

    if app.config['MAIL_USERNAME']:
        try:
            msg = Message(
                subject=f'هشدار سیستم تولید - {alert_type}',
                sender=app.config['MAIL_USERNAME'],
                recipients=[app.config['ADMIN_EMAIL']],
                body=f'{message}\n\nتاریخ: {datetime.now().strftime("%Y-%m-%d %H:%M")}'
            )
            mail.send(msg)
            alert.is_sent = True
            alert.sent_at = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            print(f"خطا در ارسال ایمیل: {e}")

def check_and_send_alerts():
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not last_date:
        return

    stop_records = StopRecord.query.filter_by(shift_date=last_date).all()
    total_stops = sum(s.stop_duration or 0 for s in stop_records)
    if total_stops > 60:
        send_alert(
            alert_type='stop',
            message=f'توقف امروز: {total_stops} دقیقه (بیش از حد مجاز)',
            severity='warning'
        )

    records = Record.query.filter_by(shift_date=last_date).all()
    eff = 0
    for rec in records:
        if 'راندمان' in rec.item.title:
            try: eff = float(rec.value)
            except: pass
            break
    if eff < 70 and eff > 0:
        send_alert(
            alert_type='efficiency',
            message=f'راندمان امروز: {eff}% (زیر ۷۰%)',
            severity='critical'
        )

    oee = calculate_oee(last_date)
    if oee < 60 and oee > 0:
        send_alert(
            alert_type='oee',
            message=f'OEE امروز: {oee}% (زیر ۶۰%)',
            severity='critical'
        )


# ===== توابع کمکی برای داشبورد مبتنی بر تگ =====

def get_value_by_tag(tag, target_date=None):
    """
    دریافت مقدار یک تگ برای تاریخ مشخص
    اگر تاریخ مشخص نشود، آخرین تاریخ موجود را می‌گیرد
    """
    if target_date is None:
        target_date = db.session.query(db.func.max(Record.shift_date)).scalar()
        if not target_date:
            return None
    
    # پیدا کردن آیتم‌های دارای این تگ
    items = Item.query.filter(Item.tags.contains(tag)).all()
    if not items:
        return None
    
    # گرفتن اولین آیتم (یا می‌توان میانگین یا جمع گرفت)
    item_ids = [i.id for i in items]
    record = Record.query.filter(
        Record.item_id.in_(item_ids),
        Record.shift_date == target_date
    ).first()
    
    if record:
        try:
            return float(record.value)
        except (ValueError, TypeError):
            return record.value
    return None

def get_all_values_by_tag(tag, start_date=None, end_date=None):
    """
    دریافت تمام مقادیر یک تگ در بازه زمانی مشخص
    """
    if end_date is None:
        end_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not end_date:
        return []
    
    if start_date is None:
        start_date = end_date - timedelta(days=30)
    
    items = Item.query.filter(Item.tags.contains(tag)).all()
    if not items:
        return []
    
    item_ids = [i.id for i in items]
    records = Record.query.filter(
        Record.item_id.in_(item_ids),
        Record.shift_date.between(start_date, end_date)
    ).order_by(Record.shift_date).all()
    
    result = []
    for rec in records:
        try:
            value = float(rec.value)
        except (ValueError, TypeError):
            value = 0
        result.append({
            'date': rec.shift_date,
            'value': value,
            'item_title': rec.item.title
        })
    return result

def get_operator_performance(operator_id, target_date=None):
    """
    دریافت عملکرد یک اپراتور در تاریخ مشخص
    """
    if target_date is None:
        target_date = db.session.query(db.func.max(Record.shift_date)).scalar()
        if not target_date:
            return {}
    
    records = Record.query.filter_by(
        operator_id=operator_id,
        shift_date=target_date
    ).all()
    
    result = {
        'efficiency': 0,
        'production_actual': 0,
        'production_plan': 0,
        'stops': 0,
        'waste': 0,
        'oee': 0
    }
    
    for rec in records:
        item = rec.item
        if not item.tags:
            continue
        
        tags = item.tags.split(',')
        try:
            value = float(rec.value)
        except (ValueError, TypeError):
            continue
        
        if 'efficiency' in tags:
            result['efficiency'] = value
        elif 'production_actual' in tags:
            result['production_actual'] = value
        elif 'production_plan' in tags:
            result['production_plan'] = value
        elif 'stop_total' in tags:
            result['stops'] = value
        elif 'waste_total' in tags:
            result['waste'] = value
    
    return result

def get_shift_performance(shift_name, target_date=None):
    """
    دریافت عملکرد یک شیفت در تاریخ مشخص
    """
    if target_date is None:
        target_date = db.session.query(db.func.max(Record.shift_date)).scalar()
        if not target_date:
            return {}
    
    records = Record.query.filter_by(
        shift_date=target_date,
        shift_name=shift_name
    ).all()
    
    result = {
        'efficiency': 0,
        'production_actual': 0,
        'stops': 0,
        'waste': 0,
        'score': 0
    }
    
    for rec in records:
        item = rec.item
        if not item.tags:
            continue
        
        tags = item.tags.split(',')
        try:
            value = float(rec.value)
        except (ValueError, TypeError):
            continue
        
        if 'efficiency' in tags:
            result['efficiency'] = value
        elif 'production_actual' in tags:
            result['production_actual'] += value
        elif 'stop_total' in tags:
            result['stops'] += value
        elif 'waste_total' in tags:
            result['waste'] += value
    
    # محاسبه امتیاز شیفت
    result['score'] = result['efficiency'] - (result['stops'] * 0.5) - (result['waste'] / 1000)
    return result


# ===== ایجاد دیتابیس و داده‌های اولیه =====
with app.app_context():
    db.create_all()

    if User.query.count() == 0:
        admin = User(username='admin', full_name='مدیر سیستم', email='admin@example.com')
        admin.set_password('123456')
        admin.role = 'admin'
        db.session.add(admin)
        db.session.commit()
        print("✅ کاربر ادمین با رمز 123456 ایجاد شد.")


    # ایجاد بخش‌ها و آیتم‌ها اگر خالی باشند
    if Section.query.count() == 0:
        # لیست بخش‌ها
        sections_data = [
            ('مخازن و آماده‌سازی', 'Mixing & Tank', 'fa-flask'),
            ('انتقال به خط', 'پمپ و لوله‌ها', 'fa-exchange-alt'),
            ('دستگاه پرکن', 'Filler', 'fa-fill-drip'),
            ('درب‌بند', 'Capper', 'fa-crown'),
            ('لیبل‌زن', 'Labeling', 'fa-tag'),
            ('شرینک پک', 'Shrink', 'fa-box'),
            ('نوار نقاله‌ها', 'Conveyor', 'fa-arrows-alt-h'),
            ('گلوگاه خط', 'Bottleneck Detection', 'fa-bottleneck'),
            ('تحلیل توقفات', 'Stop Analysis', 'fa-stopwatch'),
            ('ضایعات', 'Waste', 'fa-trash'),
            ('اپراتور و شیفت', 'Operator & Shift', 'fa-user'),
            ('هماهنگی خط', 'Line Balance', 'fa-balance-scale'),
            ('گزارش طلایی', 'Daily Golden Report', 'fa-star'),
            ('تصمیمات فوری', 'Urgent Decisions', 'fa-bolt'),
            ('تحلیل نهایی', 'Final Analysis', 'fa-chart-line'),
            ('تأیید', 'Approval', 'fa-check-circle'),
        ]
        for name, desc, icon in sections_data:
            sec = Section(name=name, description=desc, icon=icon, weight=1.0)
            db.session.add(sec)
        db.session.commit()

        # ===== 1. مخازن و آماده‌سازی =====
        sec = Section.query.filter_by(name='مخازن و آماده‌سازی').first()
        items = [
            ('زمان آماده‌سازی هر بچ', 'number', 'دقیقه', '', True, 30, 10, 'time', 'linear'),
            ('تأخیر در شارژ مواد اولیه', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('دقت بریکس', 'number', '±0.2', '', True, 100, 5, 'quality', 'sigmoid'),
            ('دقت pH', 'number', '', '', False, 7.0, 0.5, 'quality', 'sigmoid'),
            ('زمان خواب محصول قبل از انتقال', 'number', 'دقیقه', '', False, 30, 10, 'time', 'linear'),
            ('تداخل بین بچ‌ها (وقفه بین تولید)', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('آماده بودن مخزن قبل از اتمام بچ قبلی', 'checkbox', '', '', False, 1, 0, 'general', 'step'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 2. انتقال به خط =====
        sec = Section.query.filter_by(name='انتقال به خط').first()
        items = [
            ('افت فشار در مسیر', 'number', 'بار', '', False, 0, 0.5, 'quality', 'linear'),
            ('نشتی در خطوط', 'checkbox', '', '', False, 0, 0, 'quality', 'step'),
            ('هوا گرفتن سیستم', 'checkbox', '', '', False, 0, 0, 'quality', 'step'),
            ('سرعت انتقال مایع', 'number', 'لیتر/دقیقه', '', True, 100, 10, 'efficiency', 'sigmoid'),
            ('توقف به دلیل گرفتگی', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 3. دستگاه پرکن =====
        sec = Section.query.filter_by(name='دستگاه پرکن').first()
        items = [
            ('سرعت واقعی', 'number', 'بطری/دقیقه', '', True, 100, 10, 'efficiency', 'sigmoid'),
            ('اختلاف با ظرفیت اسمی', 'number', 'درصد', '', True, 0, 5, 'efficiency', 'linear'),
            ('درصد بطری ناقص پر شده', 'number', 'درصد', '', True, 0, 2, 'quality', 'sigmoid'),
            ('نشتی نازل‌ها', 'checkbox', '', '', False, 0, 0, 'quality', 'step'),
            ('توقف برای تنظیم', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('عملکرد سنسورها', 'select', '', 'عالی,متوسط,ضعیف', False, 100, 0, 'quality', 'step'),
            ('یکنواختی حجم پرکنی', 'number', 'میلی‌لیتر', '', False, 100, 5, 'quality', 'sigmoid'),
            ('راندمان پرکن', 'number', 'درصد', '', True, 90, 10, 'efficiency', 'sigmoid'),
            ('ضایعات پرکن', 'number', 'درصد', '', True, 0, 2, 'cost', 'linear'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 4. درب‌بند =====
        sec = Section.query.filter_by(name='درب‌بند').first()
        items = [
            ('شل بودن درب', 'number', 'درصد', '', False, 0, 2, 'quality', 'linear'),
            ('بیش از حد سفت شدن', 'number', 'درصد', '', False, 0, 2, 'quality', 'linear'),
            ('گیر کردن درب‌ها', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('توقف به دلیل تغذیه درب', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('هماهنگی با پرکن', 'select', '', 'عالی,متوسط,ضعیف', False, 100, 0, 'efficiency', 'step'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 5. لیبل‌زن =====
        sec = Section.query.filter_by(name='لیبل‌زن').first()
        items = [
            ('کج شدن لیبل', 'number', 'درصد', '', True, 0, 2, 'quality', 'sigmoid'),
            ('نچسبیدن کامل', 'number', 'درصد', '', True, 0, 2, 'quality', 'sigmoid'),
            ('توقف به دلیل رول لیبل', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('سرعت کمتر از خط', 'number', 'درصد', '', False, 0, 5, 'efficiency', 'linear'),
            ('مصرف بیش از حد لیبل', 'number', 'درصد', '', False, 0, 5, 'cost', 'linear'),
            ('درصد خطای لیبل', 'number', 'درصد', '', True, 0, 2, 'quality', 'sigmoid'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 6. شرینک پک =====
        sec = Section.query.filter_by(name='شرینک پک').first()
        items = [
            ('دمای تونل', 'number', 'درجه سانتی‌گراد', '', False, 190, 10, 'quality', 'sigmoid'),
            ('چروک یا پارگی نایلون', 'number', 'درصد', '', False, 0, 5, 'quality', 'linear'),
            ('توقف به دلیل گیر کردن پک', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('مصرف نایلون', 'number', 'کیلوگرم', '', False, 0, 2, 'cost', 'linear'),
            ('سرعت خروجی', 'number', 'پک/دقیقه', '', True, 100, 10, 'efficiency', 'sigmoid'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 7. نوار نقاله‌ها =====
        sec = Section.query.filter_by(name='نوار نقاله‌ها').first()
        items = [
            ('تجمع بطری (Backlog)', 'number', 'عدد', '', False, 0, 10, 'time', 'linear'),
            ('فاصله غیر استاندارد بطری‌ها', 'number', 'درصد', '', False, 0, 5, 'quality', 'linear'),
            ('توقف به دلیل گیر کردن', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('سرعت نامتوازن بین دستگاه‌ها', 'number', 'درصد', '', False, 0, 5, 'efficiency', 'linear'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 8. گلوگاه خط =====
        sec = Section.query.filter_by(name='گلوگاه خط').first()
        items = [
            ('کندترین دستگاه خط', 'text', '', '', False, 100, 0, 'efficiency', 'step'),
            ('اختلاف سرعت با سایر دستگاه‌ها', 'number', 'درصد', '', False, 0, 10, 'efficiency', 'linear'),
            ('آیا سایر دستگاه‌ها منتظر آن می‌مانند؟', 'checkbox', '', '', False, 0, 0, 'time', 'step'),
            ('چند بار در شیفت باعث توقف شده؟', 'number', 'بار', '', False, 0, 3, 'time', 'linear'),
            ('نتیجه گلوگاه', 'text', '', '', True, 100, 0, 'efficiency', 'step'),
            ('نام دستگاه گلوگاه (دقیق)', 'text', '', '', False, 100, 0, 'efficiency', 'step'),
            ('تعداد توقف‌های بحرانی', 'number', 'بار', '', False, 0, 2, 'time', 'linear'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 9. تحلیل توقفات =====
        sec = Section.query.filter_by(name='تحلیل توقفات').first()
        items = [
            ('مجموع توقف روزانه', 'number', 'دقیقه', '', True, 0, 30, 'time', 'linear'),
            ('تعداد توقف‌ها', 'number', 'عدد', '', False, 0, 5, 'time', 'linear'),
            ('بیشترین دلیل توقف: دستگاه', 'number', 'دقیقه', '', False, 0, 10, 'time', 'linear'),
            ('بیشترین دلیل توقف: مواد', 'number', 'دقیقه', '', False, 0, 10, 'time', 'linear'),
            ('بیشترین دلیل توقف: اپراتور', 'number', 'دقیقه', '', False, 0, 10, 'time', 'linear'),
            ('زمان هر توقف', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('میانگین زمان توقف', 'number', 'دقیقه', '', True, 0, 5, 'time', 'linear'),
            ('تعداد توقف‌های برنامه‌ریزی‌نشده', 'number', 'بار', '', False, 0, 3, 'time', 'linear'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 10. ضایعات =====
        sec = Section.query.filter_by(name='ضایعات').first()
        items = [
            ('بطری خراب', 'number', 'عدد', '', False, 0, 10, 'cost', 'linear'),
            ('نشتی', 'number', 'عدد', '', False, 0, 5, 'cost', 'linear'),
            ('لیبل خراب', 'number', 'عدد', '', False, 0, 10, 'cost', 'linear'),
            ('برگشتی خط', 'number', 'عدد', '', False, 0, 5, 'cost', 'linear'),
            ('کل ضایعات (ریال)', 'number', 'ریال', '', True, 0, 100000, 'cost', 'linear'),
            ('درصد ضایعات کل', 'number', 'درصد', '', True, 0, 2, 'cost', 'linear'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 11. اپراتور و شیفت =====
        sec = Section.query.filter_by(name='اپراتور و شیفت').first()
        items = [
            ('بهترین شیفت (مقایسه)', 'text', '', '', False, 100, 0, 'general', 'step'),
            ('ضعیف‌ترین اپراتور', 'text', '', '', False, 100, 0, 'general', 'step'),
            ('زمان واکنش به خطا', 'number', 'دقیقه', '', False, 0, 5, 'time', 'linear'),
            ('وابستگی خط به اپراتور خاص', 'checkbox', '', '', False, 0, 0, 'general', 'step'),
            ('امتیاز عملکرد اپراتور برتر', 'number', 'امتیاز', '', True, 100, 10, 'efficiency', 'sigmoid'),
            ('تعداد خطاهای انسانی', 'number', 'بار', '', False, 0, 3, 'quality', 'linear'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 12. هماهنگی خط =====
        sec = Section.query.filter_by(name='هماهنگی خط').first()
        items = [
            ('آیا سرعت همه دستگاه‌ها یکی است؟', 'checkbox', '', '', False, 1, 0, 'efficiency', 'step'),
            ('آیا دستگاهی همیشه بیکار می‌ماند؟', 'checkbox', '', '', False, 0, 0, 'efficiency', 'step'),
            ('آیا دستگاهی همیشه پر فشار است؟', 'checkbox', '', '', False, 0, 0, 'efficiency', 'step'),
            ('ضریب هماهنگی خط', 'number', 'درصد', '', True, 100, 10, 'efficiency', 'sigmoid'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 13. گزارش طلایی =====
        sec = Section.query.filter_by(name='گزارش طلایی').first()
        items = [
            ('تولید برنامه', 'number', 'بطری', '', True, 10000, 500, 'efficiency', 'linear'),
            ('تولید واقعی', 'number', 'بطری', '', True, 10000, 500, 'efficiency', 'sigmoid'),
            ('راندمان', 'number', 'درصد', '', True, 90, 10, 'efficiency', 'sigmoid'),
            ('توقف', 'number', 'دقیقه', '', True, 0, 30, 'time', 'linear'),
            ('ضایعات', 'number', 'درصد', '', True, 0, 2, 'cost', 'linear'),
            ('گلوگاه', 'text', '', '', True, 100, 0, 'efficiency', 'step'),
            ('بیشترین ضرر', 'text', '', '', True, 100, 0, 'cost', 'step'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 14. تصمیمات فوری =====
        sec = Section.query.filter_by(name='تصمیمات فوری').first()
        items = [
            ('افزایش سرعت دستگاه', 'text', '', '', False, 100, 0, 'efficiency', 'step'),
            ('تعویض اپراتور', 'text', '', '', False, 100, 0, 'general', 'step'),
            ('تعمیر فوری', 'text', '', '', False, 100, 0, 'general', 'step'),
            ('اصلاح فرآیند', 'text', '', '', False, 100, 0, 'general', 'step'),
            ('اولویت اقدام اصلاحی', 'select', '', 'بالا,متوسط,پایین', False, 100, 0, 'general', 'step'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 15. تحلیل نهایی =====
        sec = Section.query.filter_by(name='تحلیل نهایی').first()
        items = [
            ('افزایش تولید (در صورت رفع گلوگاه)', 'number', 'درصد', '', True, 20, 5, 'efficiency', 'sigmoid'),
            ('افزایش سود (در صورت کاهش ضایعات)', 'number', 'تومان', '', True, 1000000, 200000, 'cost', 'linear'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        # ===== 16. تأیید =====
        sec = Section.query.filter_by(name='تأیید').first()
        items = [
            ('سرپرست تولید', 'text', '', '', False, 100, 0, 'general', 'step'),
            ('مدیر کارخانه', 'text', '', '', False, 100, 0, 'general', 'step'),
            ('تاریخ', 'text', '', '', False, 100, 0, 'general', 'step'),
        ]
        for title, ftype, unit, opts, kpi, target, tol, cat, method in items:
            item = Item(
                section_id=sec.id, title=title, field_type=ftype, unit=unit,
                options=opts, is_kpi=kpi, target_value=target, tolerance=tol,
                category=cat, scoring_method=method
            )
            db.session.add(item)

        db.session.commit()
        print("✅ تمام آیتم‌های چک‌لیست با موفقیت به دیتابیس اضافه شدند.")


# ========== Global Context Processors ==========
@app.context_processor
def inject_now():
    return {'now': datetime.now()}

@app.context_processor
def inject_utility():
    def jalali(date_obj):
        if date_obj:
            return jdatetime.date.fromgregorian(date=date_obj).strftime('%Y/%m/%d')
        return ''
    return {'jalali': jalali}

@app.context_processor
def inject_user():
    if current_user.is_authenticated:
        return {'username': current_user.full_name or current_user.username}
    return {'username': 'مهمان'}

@app.template_filter('jalali')
def to_jalali(date_obj):
    if date_obj:
        return jdatetime.date.fromgregorian(date=date_obj).strftime('%Y/%m/%d')
    return ''

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            flash('✅ خوش آمدید {}'.format(user.full_name or user.username), 'success')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('❌ نام کاربری یا رمز عبور اشتباه است.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('✅ با موفقیت خارج شدید.', 'success')
    return redirect(url_for('login'))

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('لطفاً وارد شوید.', 'warning')
                return redirect(url_for('login', next=request.url))
            if current_user.role != role and current_user.role != 'admin':
                flash('شما دسترسی به این بخش را ندارید.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/')
@login_required
def index():
    check_and_send_alerts()
    
    # ===== دریافت تاریخ انتخاب‌شده =====
    selected_date_str = request.args.get('date')
    if selected_date_str:
        try:
            target_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except:
            target_date = date.today()
    else:
        target_date = date.today()
    
    # ===== دریافت آخرین تاریخ موجود در دیتابیس =====
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not last_date:
        # اگر داده‌ای وجود ندارد، پیام نمایش داده شود
        return render_template('index.html', 
                               has_data=False,
                               target_date=target_date,
                               last_date=last_date)
    
    # ===== ۱. کارت‌های آماری اصلی =====
    summary = {}
    summary['efficiency'] = get_value_by_tag('efficiency', target_date) or 0
    summary['oee'] = get_value_by_tag('oee', target_date) or 0
    summary['stop_total'] = get_value_by_tag('stop_total', target_date) or 0
    summary['production_actual'] = get_value_by_tag('production_actual', target_date) or 0
    summary['production_plan'] = get_value_by_tag('production_plan', target_date) or 0
    summary['waste_total'] = get_value_by_tag('waste_total', target_date) or 0
    summary['bottleneck'] = get_value_by_tag('bottleneck', target_date) or 'نامشخص'
    summary['line_balance'] = get_value_by_tag('line_balance', target_date) or 0
    
    # محاسبه درصد تحقق تولید
    if summary['production_plan'] > 0:
        summary['achievement'] = (summary['production_actual'] / summary['production_plan']) * 100
    else:
        summary['achievement'] = 0
    
    # ===== ۲. عملکرد شیفت‌ها =====
    shifts = ['صبح', 'عصر', 'شب']
    shift_performance = []
    for shift in shifts:
        perf = get_shift_performance(shift, target_date)
        shift_performance.append({
            'name': shift,
            'production': perf.get('production_actual', 0),
            'efficiency': perf.get('efficiency', 0),
            'stops': perf.get('stops', 0),
            'waste': perf.get('waste', 0),
            'score': perf.get('score', 0)
        })
    
    # ===== ۳. عملکرد اپراتورها =====
    operators = Operator.query.all()
    operator_performance = []
    for op in operators:
        perf = get_operator_performance(op.id, target_date)
        if any(perf.values()):  # فقط اگر داده‌ای وجود دارد
            operator_performance.append({
                'operator': op,
                'efficiency': perf.get('efficiency', 0),
                'production_actual': perf.get('production_actual', 0),
                'stops': perf.get('stops', 0),
                'waste': perf.get('waste', 0),
                'score': perf.get('efficiency', 0) - (perf.get('stops', 0) * 0.5) - (perf.get('waste', 0) / 1000)
            })
    
    # ===== ۴. آخرین توقف‌ها =====
    recent_stops = StopRecord.query.order_by(StopRecord.created_at.desc()).limit(10).all()
    
    # ===== ۵. داده‌های نمودار OEE (۷ روز اخیر) =====
    oee_trend = []
    for i in range(6, -1, -1):
        d = target_date - timedelta(days=i)
        oee_value = get_value_by_tag('oee', d) or 0
        oee_trend.append({
            'date': d,
            'oee': oee_value
        })
    
    # ===== ۶. داده‌های نمودار راندمان (۷ روز اخیر) =====
    efficiency_trend = []
    for i in range(6, -1, -1):
        d = target_date - timedelta(days=i)
        eff_value = get_value_by_tag('efficiency', d) or 0
        efficiency_trend.append({
            'date': d,
            'efficiency': eff_value
        })
    
    # ===== ۷. داده‌های نمودار توقف (۷ روز اخیر) =====
    stop_trend = []
    for i in range(6, -1, -1):
        d = target_date - timedelta(days=i)
        stop_value = get_value_by_tag('stop_total', d) or 0
        stop_trend.append({
            'date': d,
            'stops': stop_value
        })
    
    return render_template('index.html',
                           has_data=True,
                           summary=summary,
                           target_date=target_date,
                           last_date=last_date,
                           shifts=shifts,
                           shift_performance=shift_performance,
                           operators=operators,
                           operator_performance=operator_performance,
                           recent_stops=recent_stops,
                           oee_trend=oee_trend,
                           efficiency_trend=efficiency_trend,
                           stop_trend=stop_trend,
                           selected_date=selected_date_str)


def get_shift_value(records, tag):
    """دریافت مقدار یک تگ از لیست رکوردهای یک شیفت"""
    # پیدا کردن آیتم‌های دارای این تگ
    items = Item.query.filter(Item.tags.contains(tag)).all()
    item_ids = [i.id for i in items]
    
    for rec in records:
        if rec.item_id in item_ids:
            try:
                return float(rec.value)
            except:
                return rec.value
    return 0


import jdatetime

@app.route('/record/new', methods=['GET', 'POST'])
@login_required
def add_record():
    operators = Operator.query.all()
    
    if request.method == 'POST':
        shift_date_persian = request.form.get('shift_date_persian')
        shift_name = request.form.get('shift_name')
        operator_id = request.form.get('operator_id')
        
        if not shift_date_persian:
            flash('لطفاً تاریخ را به شمسی وارد کنید.', 'danger')
            return redirect(url_for('add_record'))
        
        try:
            parts = shift_date_persian.split('/')
            if len(parts) == 3:
                y, m, d = map(int, parts)
                gregorian_date = jdatetime.date(y, m, d).togregorian()
            else:
                flash('فرمت تاریخ نامعتبر است. مثال: ۱۴۰۴/۰۵/۲۵', 'danger')
                return redirect(url_for('add_record'))
        except Exception:
            flash('تاریخ وارد شده معتبر نیست. مثال: ۱۴۰۴/۰۵/۲۵', 'danger')
            return redirect(url_for('add_record'))
        
        # ===== دریافت آیتم‌های مرتبط با اپراتور =====
        selected_operator = None
        relevant_items = []
        if operator_id:
            selected_operator = Operator.query.get(int(operator_id))
            if selected_operator:
                responsibilities = Responsibility.query.filter_by(
                    person_type='operator', 
                    person_id=selected_operator.id
                ).all()
                section_ids = [r.section_id for r in responsibilities]
                if section_ids:
                    relevant_items = Item.query.filter(Item.section_id.in_(section_ids)).all()
        
        if not relevant_items:
            relevant_items = Item.query.all()
        
        # ===== بررسی کامل بودن آیتم‌ها (به جز چک‌باکس) =====
        missing_items = []
        for item in relevant_items:
            if item.field_type == 'checkbox':
                continue
            value = request.form.get(f'item_{item.id}')
            if value is None or value.strip() == '':
                missing_items.append(item.title)
        
        if missing_items:
            flash(f'❌ لطفاً تمام آیتم‌ها را پر کنید. آیتم‌های زیر مقدار ندارند: {", ".join(missing_items[:5])}', 'danger')
            return redirect(url_for('add_record', operator_id=operator_id))
        
        # ===== ذخیره رکوردها =====
        all_items = Item.query.all()
        relevant_item_ids = {item.id for item in relevant_items}
        operator_id_to_save = int(operator_id) if operator_id else None
        
        for item in all_items:
            value = request.form.get(f'item_{item.id}')
            if item.field_type == 'checkbox':
                value = '1' if value == 'on' else '0'
            elif value is None:
                value = item.default_value or ''
            
            # فقط برای آیتم‌های مرتبط با اپراتور، operator_id را ذخیره کن
            op_id_for_record = operator_id_to_save if item.id in relevant_item_ids else None
            
            record = Record(
                item_id=item.id,
                shift_date=gregorian_date,
                shift_name=shift_name,
                operator_id=op_id_for_record,
                value=value,
                changed_by=current_user.id,
                change_reason='ثبت جدید'
            )
            db.session.add(record)
        
        db.session.commit()
        flash('✅ رکورد با موفقیت ثبت شد', 'success')
        return redirect(url_for('records_list'))
    
    # ===== نمایش فرم (GET) =====
    selected_operator_id = request.args.get('operator_id', type=int)
    filtered_sections = []
    if selected_operator_id:
        selected_operator = Operator.query.get(selected_operator_id)
        if selected_operator:
            responsibilities = Responsibility.query.filter_by(
                person_type='operator', 
                person_id=selected_operator.id
            ).all()
            section_ids = [r.section_id for r in responsibilities]
            if section_ids:
                filtered_sections = Section.query.filter(Section.id.in_(section_ids)).all()
    
    if not filtered_sections:
        filtered_sections = Section.query.all()
    
    total_items = sum(len(section.items) for section in filtered_sections)
    today_persian = jdatetime.date.today().strftime('%Y/%m/%d')
    
    return render_template(
        'add_record.html', 
        sections=filtered_sections, 
        operators=operators,
        total_items=total_items,
        selected_operator_id=selected_operator_id,
        today_persian=today_persian
    )




@app.route('/record/copy_previous', methods=['POST'])
@login_required
def copy_previous_record():
    # دریافت تاریخ شمسی از فرم
    shift_date_persian = request.form.get('shift_date_persian')
    
    if not shift_date_persian:
        flash('لطفاً تاریخ را به شمسی وارد کنید.', 'danger')
        return redirect(url_for('add_record'))
    
    # تبدیل تاریخ شمسی به میلادی
    try:
        parts = shift_date_persian.split('/')
        if len(parts) == 3:
            y, m, d = map(int, parts)
            current_date = jdatetime.date(y, m, d).togregorian()
        else:
            flash('فرمت تاریخ نامعتبر است. مثال: ۱۴۰۴/۰۵/۲۵', 'danger')
            return redirect(url_for('add_record'))
    except Exception as e:
        flash('تاریخ وارد شده معتبر نیست. مثال: ۱۴۰۴/۰۵/۲۵', 'danger')
        return redirect(url_for('add_record'))
    
    # محاسبه روز قبل
    prev_date = current_date - timedelta(days=1)
    
    # دریافت رکوردهای روز قبل
    prev_records = Record.query.filter_by(shift_date=prev_date).all()
    if not prev_records:
        flash('رکوردی برای روز قبل وجود ندارد.', 'warning')
        return redirect(url_for('add_record'))
    
    # کپی رکوردها
    for rec in prev_records:
        new_rec = Record(
            item_id=rec.item_id,
            shift_date=current_date,
            shift_name=rec.shift_name,
            operator_id=rec.operator_id,
            value=rec.value,
            changed_by=current_user.id,
            change_reason=f'کپی از روز {prev_date}'
        )
        db.session.add(new_rec)
    
    db.session.commit()
    flash(f'✅ رکوردهای روز {shift_date_persian} با موفقیت کپی شدند.', 'success')
    return redirect(url_for('add_record'))


@app.route('/record/edit/<int:record_id>', methods=['GET', 'POST'])
@login_required
def edit_record(record_id):
    record = Record.query.get_or_404(record_id)
    if request.method == 'POST':
        value = request.form.get('value')
        if value is None:
            flash('مقدار را وارد کنید.', 'danger')
            return redirect(url_for('edit_record', record_id=record_id))
        record.value = value
        record.updated_at = datetime.utcnow()
        record.changed_by = current_user.id
        record.change_reason = 'ویرایش دستی'
        db.session.commit()
        flash('رکورد ویرایش شد.', 'success')
        return redirect(url_for('records_list'))
    return render_template('edit_record.html', record=record)

@app.route('/record/delete/<int:record_id>', methods=['POST'])
@login_required
def delete_record(record_id):
    record = Record.query.get_or_404(record_id)
    db.session.delete(record)
    db.session.commit()
    flash('رکورد حذف شد.', 'success')
    return redirect(url_for('records_list'))

@app.route('/records')
@login_required
def records_list():
    date_filter = request.args.get('date')
    shift_filter = request.args.get('shift')
    operator_filter = request.args.get('operator_id')
    search_q = request.args.get('q')
    query = Record.query
    if date_filter:
        query = query.filter(Record.shift_date == datetime.strptime(date_filter, '%Y-%m-%d').date())
    if shift_filter:
        query = query.filter(Record.shift_name == shift_filter)
    if operator_filter:
        query = query.filter(Record.operator_id == int(operator_filter))
    if search_q:
        query = query.join(Item).filter(Item.title.contains(search_q))
    records = query.order_by(Record.created_at.desc()).all()
    operators = Operator.query.all()
    return render_template('records_list.html', records=records, operators=operators)

@app.route('/report')
@login_required
def report():
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not last_date:
        flash('هنوز رکوردی ثبت نشده است', 'warning')
        return redirect(url_for('index'))
    
    records = Record.query.filter(Record.shift_date == last_date).all()
    report_data = {rec.item.title: rec.value for rec in records}
    
    # تابع کمکی برای تبدیل مطمئن به عدد
    def safe_float(value, default=0.0):
        if value is None:
            return default
        try:
            # حذف کاراکترهای غیرعددی (به جز نقطه)
            cleaned = ''.join(c for c in str(value) if c.isdigit() or c == '.')
            return float(cleaned) if cleaned else default
        except (ValueError, TypeError):
            return default
    
    def safe_int(value, default=0):
        if value is None:
            return default
        try:
            cleaned = ''.join(c for c in str(value) if c.isdigit())
            return int(cleaned) if cleaned else default
        except (ValueError, TypeError):
            return default
    
    plan = safe_float(report_data.get('تولید برنامه', 1))
    actual = safe_float(report_data.get('تولید واقعی', 0))
    efficiency = (actual / plan * 100) if plan else 0
    stops = safe_int(report_data.get('مجموع توقف روزانه', 0))
    waste = safe_float(report_data.get('کل ضایعات (ریال)', 0))
    bottleneck = report_data.get('گلوگاه', 'نامشخص')
    
    return render_template('report.html',
                           efficiency=efficiency,
                           stops=stops,
                           waste=waste,
                           bottleneck=bottleneck,
                           record_date=last_date)



@app.route('/analysis')
@login_required
def analysis():
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not last_date:
        flash('داده‌ای برای تحلیل وجود ندارد', 'warning')
        return redirect(url_for('index'))
    speed_items = Item.query.filter(Item.title.contains('سرعت')).all()
    speeds = {}
    for item in speed_items:
        rec = Record.query.filter_by(item_id=item.id, shift_date=last_date).first()
        if rec:
            try:
                speeds[item.section.name] = float(rec.value)
            except:
                pass
    slowest = min(speeds, key=speeds.get) if speeds else 'نامشخص'
    fastest = max(speeds, key=speeds.get) if speeds else 'نامشخص'
    return render_template('analysis.html', slowest=slowest, fastest=fastest)

@app.route('/operators', methods=['GET', 'POST'])
@login_required
def operators():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form.get('name')
            code = request.form.get('code')
            hire_date = request.form.get('hire_date')
            skill = request.form.get('skill_level')
            shift_pref = request.form.get('shift_preference')
            supervisor_id = request.form.get('supervisor_id')
            op = Operator(
                name=name,
                code=code,
                hire_date=datetime.strptime(hire_date, '%Y-%m-%d').date() if hire_date else None,
                skill_level=skill,
                shift_preference=shift_pref,
                supervisor_id=int(supervisor_id) if supervisor_id else None
            )
            db.session.add(op)
            db.session.commit()
            flash('اپراتور اضافه شد', 'success')
        elif action == 'delete':
            op_id = request.form.get('operator_id')
            op = Operator.query.get(op_id)
            if op:
                db.session.delete(op)
                db.session.commit()
                flash('اپراتور حذف شد', 'success')
        return redirect(url_for('operators'))
    all_operators = Operator.query.all()
    supervisors = Supervisor.query.all()
    return render_template('operators.html', operators=all_operators, supervisors=supervisors)

@app.route('/operator_performance')
@login_required
def operator_performance():
    operators = Operator.query.all()
    performance = []
    for op in operators:
        records = Record.query.filter_by(operator_id=op.id).all()
        eff_values = []
        for rec in records:
            if 'راندمان' in rec.item.title:
                try:
                    eff_values.append(float(rec.value))
                except:
                    pass
        avg_eff = sum(eff_values)/len(eff_values) if eff_values else 0
        stop_count = StopRecord.query.filter_by(operator_id=op.id).count()
        performance.append({
            'operator': op,
            'avg_efficiency': avg_eff,
            'stop_count': stop_count,
            'score': avg_eff - (stop_count * 2)
        })
    return render_template('operator_performance.html', performance=performance)

@app.route('/supervisors', methods=['GET', 'POST'])
@login_required
def supervisors():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form.get('name')
            code = request.form.get('code')
            email = request.form.get('email')
            phone = request.form.get('phone')
            hire_date = request.form.get('hire_date')
            sup = Supervisor(
                name=name,
                code=code,
                email=email,
                phone=phone,
                hire_date=datetime.strptime(hire_date, '%Y-%m-%d').date() if hire_date else None
            )
            db.session.add(sup)
            db.session.commit()
            flash('مسئول با موفقیت اضافه شد', 'success')
        elif action == 'delete':
            sup_id = request.form.get('supervisor_id')
            sup = Supervisor.query.get(sup_id)
            if sup:
                db.session.delete(sup)
                db.session.commit()
                flash('مسئول حذف شد', 'success')
        return redirect(url_for('supervisors'))
    supervisors = Supervisor.query.all()
    return render_template('supervisors.html', supervisors=supervisors)

@app.route('/assignments', methods=['GET', 'POST'])
@login_required
def assignments():
    operators = Operator.query.all()
    supervisors = Supervisor.query.all()
    sections = Section.query.all()
    
    if request.method == 'POST':
        person_type = request.form.get('person_type')
        person_id = request.form.get('person_id')
        section_ids = request.form.getlist('section_ids')
        
        Responsibility.query.filter_by(person_type=person_type, person_id=person_id).delete()
        
        for sec_id in section_ids:
            if sec_id:
                resp = Responsibility(
                    person_type=person_type,
                    person_id=int(person_id),
                    section_id=int(sec_id)
                )
                db.session.add(resp)
        db.session.commit()
        flash('انتسابات با موفقیت ذخیره شد', 'success')
        return redirect(url_for('assignments'))
    
    assignments_dict = {}
    for op in operators:
        key = f'operator_{op.id}'
        resp_list = Responsibility.query.filter_by(person_type='operator', person_id=op.id).all()
        assignments_dict[key] = [r.section_id for r in resp_list]
    for sup in supervisors:
        key = f'supervisor_{sup.id}'
        resp_list = Responsibility.query.filter_by(person_type='supervisor', person_id=sup.id).all()
        assignments_dict[key] = [r.section_id for r in resp_list]
    
    return render_template('assignments.html', operators=operators, supervisors=supervisors,
                           sections=sections, assignments=assignments_dict)

@app.route('/evaluation')
@login_required
def evaluation():
    operators = Operator.query.all()
    supervisors = Supervisor.query.all()
    
    person_performance = []
    
    for op in operators:
        sections = [r.section for r in Responsibility.query.filter_by(person_type='operator', person_id=op.id).all()]
        perf = calculate_person_performance(op, sections, 'operator')
        person_performance.append(perf)
    
    for sup in supervisors:
        sections = [r.section for r in Responsibility.query.filter_by(person_type='supervisor', person_id=sup.id).all()]
        perf = calculate_person_performance(sup, sections, 'supervisor')
        person_performance.append(perf)
    
    all_sections = Section.query.all()
    overall = calculate_overall_performance(all_sections)
    
    return render_template('evaluation.html', person_performance=person_performance, overall=overall)

@app.route('/item_weights', methods=['GET', 'POST'])
@login_required
def item_weights():
    if request.method == 'POST':
        item_id = request.form.get('item_id')
        weight = request.form.get('weight')
        impact_type = request.form.get('impact_type')
        target_value = request.form.get('target_value')
        tolerance = request.form.get('tolerance')
        category = request.form.get('category')
        scoring_method = request.form.get('scoring_method')
        
        item = Item.query.get(item_id)
        if item:
            item.weight = float(weight) if weight else 1.0
            item.impact_type = impact_type
            item.target_value = float(target_value) if target_value else None
            item.tolerance = float(tolerance) if tolerance else 10.0
            item.category = category
            item.scoring_method = scoring_method
            db.session.commit()
            flash('تنظیمات آیتم با موفقیت به‌روزرسانی شد', 'success')
        return redirect(url_for('item_weights'))
    
    items = Item.query.all()
    sections = Section.query.all()
    return render_template('item_weights.html', items=items, sections=sections)

@app.route('/smart_report')
@login_required
def smart_report():
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not last_date:
        flash('هنوز داده‌ای برای تحلیل وجود ندارد', 'warning')
        return redirect(url_for('index'))
    # دریافت مقادیر بر اساس تگ‌ها
    analysis = {
        'efficiency': get_value_by_tag('efficiency', last_date) or 0,
        'stops': get_value_by_tag('stop_total', last_date) or 0,
        'waste': get_value_by_tag('waste_total', last_date) or 0,
        'bottleneck': get_value_by_tag('bottleneck', last_date) or 'نامشخص',
        'oee': get_value_by_tag('oee', last_date) or calculate_oee(last_date) or 0,
        'production_plan': get_value_by_tag('production_plan', last_date) or 0,
        'production_actual': get_value_by_tag('production_actual', last_date) or 0,
        'line_balance': get_value_by_tag('line_balance', last_date) or 0,
    }
    
    # محاسبه OEE اگر موجود نبود
    if analysis['oee'] == 0:
        analysis['oee'] = calculate_oee(last_date)
    
    
    records = Record.query.filter(Record.shift_date == last_date).all()
    report_data = {rec.item.title: rec.value for rec in records}
    
    analysis = {
        'efficiency': 0,
        'stops': 0,
        'waste': 0,
        'bottleneck': 'نامشخص',
        'oee': 0,
        'production_plan': 0,
        'production_actual': 0,
        'operator_score': 0,
        'line_balance': 0,
    }
    
    try:
        analysis['production_plan'] = float(report_data.get('تولید برنامه', 0))
        analysis['production_actual'] = float(report_data.get('تولید واقعی', 0))
        analysis['efficiency'] = float(report_data.get('راندمان', 0))
        analysis['stops'] = float(report_data.get('مجموع توقف روزانه', 0))
        analysis['waste'] = float(report_data.get('کل ضایعات (ریال)', 0))
        analysis['bottleneck'] = report_data.get('گلوگاه', 'نامشخص')
        analysis['oee'] = calculate_oee(last_date)
        analysis['line_balance'] = float(report_data.get('ضریب هماهنگی خط', 0)) if report_data.get('ضریب هماهنگی خط') else 0
    except:
        pass
    
    recommendations = []
    
    if analysis['efficiency'] < 70:
        recommendations.append({
            'title': '🔴 افزایش راندمان خط',
            'description': 'راندمان فعلی {:.1f}% است که بسیار پایین است. گلوگاه اصلی را شناسایی و برطرف کنید.'.format(analysis['efficiency']),
            'action': 'بررسی دستگاه پرکن و نوار نقاله‌ها'
        })
    elif analysis['efficiency'] < 85:
        recommendations.append({
            'title': '🟡 بهبود راندمان',
            'description': 'راندمان فعلی {:.1f}% است. با بهینه‌سازی زمان‌های توقف می‌توان آن را افزایش داد.'.format(analysis['efficiency']),
            'action': 'کاهش توقفات غیربرنامه‌ریزی‌شده'
        })
    else:
        recommendations.append({
            'title': '🟢 راندمان عالی',
            'description': 'راندمان فعلی {:.1f}% است. عملکرد خط تولید مطلوب است.'.format(analysis['efficiency']),
            'action': 'حفظ وضعیت و پایش مستمر'
        })
    
    if analysis['stops'] > 60:
        recommendations.append({
            'title': '🔴 کاهش توقفات',
            'description': 'مجموع توقفات {:.0f} دقیقه است که بسیار زیاد است.'.format(analysis['stops']),
            'action': 'برنامه نگهداری پیشگیرانه برای دستگاه‌های پرتوقف'
        })
    elif analysis['stops'] > 30:
        recommendations.append({
            'title': '🟡 مدیریت توقفات',
            'description': 'مجموع توقفات {:.0f} دقیقه است.'.format(analysis['stops']),
            'action': 'بررسی علل اصلی و کاهش زمان تعمیرات'
        })
    else:
        recommendations.append({
            'title': '🟢 توقفات قابل‌قبول',
            'description': 'مجموع توقفات {:.0f} دقیقه است.'.format(analysis['stops']),
            'action': 'ادامه روند فعلی'
        })
    
    if analysis['waste'] > 500000:
        recommendations.append({
            'title': '🔴 کاهش ضایعات',
            'description': 'ضایعات {:.0f} ریال است. آموزش اپراتورها و تنظیم دقیق دستگاه‌ها ضروری است.'.format(analysis['waste']),
            'action': 'آموزش مجدد اپراتورها و کالیبراسیون دستگاه‌ها'
        })
    elif analysis['waste'] > 200000:
        recommendations.append({
            'title': '🟡 کنترل ضایعات',
            'description': 'ضایعات {:.0f} ریال است.'.format(analysis['waste']),
            'action': 'بررسی فرآیندهای پرخطا'
        })
    else:
        recommendations.append({
            'title': '🟢 ضایعات کم',
            'description': 'ضایعات {:.0f} ریال است.'.format(analysis['waste']),
            'action': 'ادامه کنترل کیفیت'
        })
    
    if analysis['bottleneck'] and analysis['bottleneck'] != 'نامشخص':
        recommendations.append({
            'title': '⚙️ رفع گلوگاه',
            'description': 'گلوگاه خط: {}'.format(analysis['bottleneck']),
            'action': 'بررسی و بهینه‌سازی دستگاه {}'.format(analysis['bottleneck'])
        })
    
    if analysis['oee'] == 0:
        analysis['oee'] = calculate_oee(last_date)
    
    
    # روند راندمان با استفاده از تگ
    trends = []
    for i in range(7, 0, -1):
        d = last_date - timedelta(days=i)
        eff = get_value_by_tag('efficiency', d) or 0
        persian_date = jdatetime.date.fromgregorian(date=d).strftime('%Y/%m/%d')
        trends.append({'date': persian_date, 'efficiency': eff})
    
    return render_template('smart_report.html',
                           analysis=analysis,
                           recommendations=recommendations,
                           trends=trends,
                           record_date=last_date)



@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_item':
            section_id = request.form.get('section_id')
            title = request.form.get('title')
            field_type = request.form.get('field_type')
            unit = request.form.get('unit')
            default_value = request.form.get('default_value')
            options = request.form.get('options')
            is_kpi = bool(request.form.get('is_kpi'))
            if section_id and title:
                item = Item(
                    section_id=int(section_id),
                    title=title,
                    field_type=field_type,
                    unit=unit,
                    default_value=default_value,
                    options=options,
                    is_kpi=is_kpi
                )
                db.session.add(item)
                db.session.commit()
                flash('آیتم جدید اضافه شد', 'success')
        elif action == 'delete_item':
            item_id = request.form.get('item_id')
            item = Item.query.get(item_id)
            if item:
                db.session.delete(item)
                db.session.commit()
                flash('آیتم حذف شد', 'success')
        elif action == 'add_section':
            name = request.form.get('name')
            description = request.form.get('description')
            icon = request.form.get('icon')
            if name:
                section = Section(name=name, description=description, icon=icon)
                db.session.add(section)
                db.session.commit()
                flash('بخش جدید اضافه شد', 'success')
        elif action == 'delete_section':
            section_id = request.form.get('section_id')
            section = Section.query.get(section_id)
            if section:
                db.session.delete(section)
                db.session.commit()
                flash('بخش حذف شد', 'success')
        return redirect(url_for('settings'))
    
    sections = Section.query.all()
    items = Item.query.all()
    total_items = sum(len(section.items) for section in sections)  # ← اضافه شده

    return render_template('settings.html', sections=sections, items=items, total_items=total_items)  # ← اضافه شده


@app.route('/edit_item/<int:item_id>', methods=['GET', 'POST'])
@login_required
def edit_item(item_id):
    """ویرایش آیتم موجود"""
    item = Item.query.get_or_404(item_id)
    
    if request.method == 'POST':
        # دریافت مقادیر از فرم
        section_id = request.form.get('section_id')
        title = request.form.get('title')
        field_type = request.form.get('field_type')
        unit = request.form.get('unit')
        default_value = request.form.get('default_value')
        options = request.form.get('options')
        is_kpi = bool(request.form.get('is_kpi'))
        target_value = request.form.get('target_value')
        tolerance = request.form.get('tolerance')
        category = request.form.get('category')
        scoring_method = request.form.get('scoring_method')
        tags = request.form.getlist('tags')
        
        # اعتبارسنجی ساده
        if not title or not section_id:
            flash('عنوان و بخش اجباری هستند.', 'danger')
            return redirect(url_for('settings'))
        
        # به‌روزرسانی آیتم
        try:
            item.section_id = int(section_id)
            item.title = title
            item.field_type = field_type
            item.unit = unit
            item.default_value = default_value
            item.options = options
            item.is_kpi = is_kpi
            item.target_value = float(target_value) if target_value and target_value.strip() else None
            item.tolerance = float(tolerance) if tolerance and tolerance.strip() else 10.0
            item.category = category if category else 'general'
            item.scoring_method = scoring_method if scoring_method else 'linear'
            item.tags = ','.join(tags) if tags else ''
            
            db.session.commit()
            flash('✅ آیتم با موفقیت ویرایش شد.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'❌ خطا در ویرایش آیتم: {str(e)}', 'danger')
        
        return redirect(url_for('settings'))
    
    # اگر درخواست GET باشد، به صفحه تنظیمات بازگردانده می‌شود
    return redirect(url_for('settings'))



@app.route('/final_report')
@login_required
def final_report():
    operators = Operator.query.all()
    supervisors = Supervisor.query.all()
    
    person_performance = []
    for op in operators:
        sections = [r.section for r in Responsibility.query.filter_by(person_type='operator', person_id=op.id).all()]
        perf = calculate_person_performance(op, sections, 'operator')
        person_performance.append(perf)
    
    for sup in supervisors:
        sections = [r.section for r in Responsibility.query.filter_by(person_type='supervisor', person_id=sup.id).all()]
        perf = calculate_person_performance(sup, sections, 'supervisor')
        person_performance.append(perf)
    
    operator_scores = [p['score'] for p in person_performance if p['type'] == 'operator']
    supervisor_scores = [p['score'] for p in person_performance if p['type'] == 'supervisor']
    avg_operator_score = sum(operator_scores) / len(operator_scores) if operator_scores else 0
    avg_supervisor_score = sum(supervisor_scores) / len(supervisor_scores) if supervisor_scores else 0
    
    return render_template('final_report.html',
                           person_performance=person_performance,
                           operators=operators,
                           supervisors=supervisors,
                           avg_operator_score=avg_operator_score,
                           avg_supervisor_score=avg_supervisor_score)

@app.route('/api/oee_data')
@login_required
def oee_data():
    end_date = date.today()
    start_date = end_date - timedelta(days=7)
    dates = []
    oee_values = []
    for d in (start_date + timedelta(n) for n in range(8)):
        dates.append(d.isoformat())
        oee = calculate_oee(d)
        oee_values.append(oee)
    return jsonify({'dates': dates, 'oee': oee_values})

@app.route('/api/operator_performance')
@login_required
def api_operator_performance():
    operators = Operator.query.all()
    names = []
    scores = []
    for op in operators:
        records = Record.query.filter_by(operator_id=op.id).all()
        eff_values = []
        for rec in records:
            if 'راندمان' in rec.item.title:
                try:
                    eff_values.append(float(rec.value))
                except:
                    pass
        avg_eff = sum(eff_values)/len(eff_values) if eff_values else 0
        stop_count = StopRecord.query.filter_by(operator_id=op.id).count()
        score = avg_eff - (stop_count * 2)
        names.append(op.name)
        scores.append(round(score, 2))
    return jsonify({'names': names, 'scores': scores})

@app.route('/update_item_settings')
@login_required
def update_item_settings():
    """
    مسیر موقت برای به‌روزرسانی تنظیمات تمام آیتم‌ها با یک کوئری
    (فقط یک بار اجرا کنید و سپس این مسیر را حذف یا غیرفعال کنید)
    """
    if current_user.role != 'admin':
        flash('فقط ادمین می‌تواند این کار را انجام دهد.', 'danger')
        return redirect(url_for('index'))
    
    # دیکشنری تنظیمات (بر اساس عنوان آیتم)
    settings_map = {
        # === مخازن و آماده‌سازی ===
        'زمان آماده‌سازی هر بچ': {'target': 30, 'tolerance': 10, 'category': 'time', 'method': 'linear'},
        'تأخیر در شارژ مواد اولیه': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'دقت بریکس': {'target': 100, 'tolerance': 5, 'category': 'quality', 'method': 'sigmoid'},
        'دقت pH': {'target': 7.0, 'tolerance': 0.5, 'category': 'quality', 'method': 'sigmoid'},
        'زمان خواب محصول قبل از انتقال': {'target': 30, 'tolerance': 10, 'category': 'time', 'method': 'linear'},
        'تداخل بین بچ‌ها (وقفه بین تولید)': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'آماده بودن مخزن قبل از اتمام بچ قبلی': {'target': 1, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        
        # === انتقال به خط ===
        'افت فشار در مسیر': {'target': 0, 'tolerance': 0.5, 'category': 'quality', 'method': 'linear'},
        'نشتی در خطوط': {'target': 0, 'tolerance': 0, 'category': 'quality', 'method': 'step'},
        'هوا گرفتن سیستم': {'target': 0, 'tolerance': 0, 'category': 'quality', 'method': 'step'},
        'سرعت انتقال مایع': {'target': 100, 'tolerance': 10, 'category': 'efficiency', 'method': 'sigmoid'},
        'توقف به دلیل گرفتگی': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        
        # === دستگاه پرکن ===
        'سرعت واقعی': {'target': 100, 'tolerance': 10, 'category': 'efficiency', 'method': 'sigmoid'},
        'اختلاف با ظرفیت اسمی': {'target': 0, 'tolerance': 5, 'category': 'efficiency', 'method': 'linear'},
        'درصد بطری ناقص پر شده': {'target': 0, 'tolerance': 2, 'category': 'quality', 'method': 'sigmoid'},
        'نشتی نازل‌ها': {'target': 0, 'tolerance': 0, 'category': 'quality', 'method': 'step'},
        'توقف برای تنظیم': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'عملکرد سنسورها': {'target': 100, 'tolerance': 0, 'category': 'quality', 'method': 'step'},
        'یکنواختی حجم پرکنی': {'target': 100, 'tolerance': 5, 'category': 'quality', 'method': 'sigmoid'},
        'راندمان پرکن': {'target': 90, 'tolerance': 10, 'category': 'efficiency', 'method': 'sigmoid'},
        'ضایعات پرکن': {'target': 0, 'tolerance': 2, 'category': 'cost', 'method': 'linear'},
        
        # === درب‌بند ===
        'شل بودن درب': {'target': 0, 'tolerance': 2, 'category': 'quality', 'method': 'linear'},
        'بیش از حد سفت شدن': {'target': 0, 'tolerance': 2, 'category': 'quality', 'method': 'linear'},
        'گیر کردن درب‌ها': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'توقف به دلیل تغذیه درب': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'هماهنگی با پرکن': {'target': 100, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        
        # === لیبل‌زن ===
        'کج شدن لیبل': {'target': 0, 'tolerance': 2, 'category': 'quality', 'method': 'sigmoid'},
        'نچسبیدن کامل': {'target': 0, 'tolerance': 2, 'category': 'quality', 'method': 'sigmoid'},
        'توقف به دلیل رول لیبل': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'سرعت کمتر از خط': {'target': 0, 'tolerance': 5, 'category': 'efficiency', 'method': 'linear'},
        'مصرف بیش از حد لیبل': {'target': 0, 'tolerance': 5, 'category': 'cost', 'method': 'linear'},
        'درصد خطای لیبل': {'target': 0, 'tolerance': 2, 'category': 'quality', 'method': 'sigmoid'},
        
        # === شرینک پک ===
        'دمای تونل': {'target': 190, 'tolerance': 10, 'category': 'quality', 'method': 'sigmoid'},
        'چروک یا پارگی نایلون': {'target': 0, 'tolerance': 5, 'category': 'quality', 'method': 'linear'},
        'توقف به دلیل گیر کردن پک': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'مصرف نایلون': {'target': 0, 'tolerance': 2, 'category': 'cost', 'method': 'linear'},
        'سرعت خروجی': {'target': 100, 'tolerance': 10, 'category': 'efficiency', 'method': 'sigmoid'},
        
        # === نوار نقاله‌ها ===
        'تجمع بطری (Backlog)': {'target': 0, 'tolerance': 10, 'category': 'time', 'method': 'linear'},
        'فاصله غیر استاندارد بطری‌ها': {'target': 0, 'tolerance': 5, 'category': 'quality', 'method': 'linear'},
        'توقف به دلیل گیر کردن': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'سرعت نامتوازن بین دستگاه‌ها': {'target': 0, 'tolerance': 5, 'category': 'efficiency', 'method': 'linear'},
        
        # === گلوگاه خط ===
        'کندترین دستگاه خط': {'target': 100, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        'اختلاف سرعت با سایر دستگاه‌ها': {'target': 0, 'tolerance': 10, 'category': 'efficiency', 'method': 'linear'},
        'آیا سایر دستگاه‌ها منتظر آن می‌مانند؟': {'target': 0, 'tolerance': 0, 'category': 'time', 'method': 'step'},
        'چند بار در شیفت باعث توقف شده؟': {'target': 0, 'tolerance': 3, 'category': 'time', 'method': 'linear'},
        'نتیجه گلوگاه': {'target': 100, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        'نام دستگاه گلوگاه (دقیق)': {'target': 100, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        'تعداد توقف‌های بحرانی': {'target': 0, 'tolerance': 2, 'category': 'time', 'method': 'linear'},
        
        # === تحلیل توقفات ===
        'مجموع توقف روزانه': {'target': 0, 'tolerance': 30, 'category': 'time', 'method': 'linear'},
        'تعداد توقف‌ها': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'بیشترین دلیل توقف: دستگاه': {'target': 0, 'tolerance': 10, 'category': 'time', 'method': 'linear'},
        'بیشترین دلیل توقف: مواد': {'target': 0, 'tolerance': 10, 'category': 'time', 'method': 'linear'},
        'بیشترین دلیل توقف: اپراتور': {'target': 0, 'tolerance': 10, 'category': 'time', 'method': 'linear'},
        'زمان هر توقف': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'میانگین زمان توقف': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'تعداد توقف‌های برنامه‌ریزی‌نشده': {'target': 0, 'tolerance': 3, 'category': 'time', 'method': 'linear'},
        
        # === ضایعات ===
        'بطری خراب': {'target': 0, 'tolerance': 10, 'category': 'cost', 'method': 'linear'},
        'نشتی': {'target': 0, 'tolerance': 5, 'category': 'cost', 'method': 'linear'},
        'لیبل خراب': {'target': 0, 'tolerance': 10, 'category': 'cost', 'method': 'linear'},
        'برگشتی خط': {'target': 0, 'tolerance': 5, 'category': 'cost', 'method': 'linear'},
        'کل ضایعات (ریال)': {'target': 0, 'tolerance': 100000, 'category': 'cost', 'method': 'linear'},
        'درصد ضایعات کل': {'target': 0, 'tolerance': 2, 'category': 'cost', 'method': 'linear'},
        
        # === اپراتور و شیفت ===
        'بهترین شیفت (مقایسه)': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        'ضعیف‌ترین اپراتور': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        'زمان واکنش به خطا': {'target': 0, 'tolerance': 5, 'category': 'time', 'method': 'linear'},
        'وابستگی خط به اپراتور خاص': {'target': 0, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        'امتیاز عملکرد اپراتور برتر': {'target': 100, 'tolerance': 10, 'category': 'efficiency', 'method': 'sigmoid'},
        'تعداد خطاهای انسانی': {'target': 0, 'tolerance': 3, 'category': 'quality', 'method': 'linear'},
        
        # === هماهنگی خط ===
        'آیا سرعت همه دستگاه‌ها یکی است؟': {'target': 1, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        'آیا دستگاهی همیشه بیکار می‌ماند؟': {'target': 0, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        'آیا دستگاهی همیشه پر فشار است؟': {'target': 0, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        'ضریب هماهنگی خط': {'target': 100, 'tolerance': 10, 'category': 'efficiency', 'method': 'sigmoid'},
        
        # === گزارش طلایی ===
        'تولید برنامه': {'target': 10000, 'tolerance': 500, 'category': 'efficiency', 'method': 'linear'},
        'تولید واقعی': {'target': 10000, 'tolerance': 500, 'category': 'efficiency', 'method': 'sigmoid'},
        'راندمان': {'target': 90, 'tolerance': 10, 'category': 'efficiency', 'method': 'sigmoid'},
        'توقف': {'target': 0, 'tolerance': 30, 'category': 'time', 'method': 'linear'},
        'ضایعات': {'target': 0, 'tolerance': 2, 'category': 'cost', 'method': 'linear'},
        'گلوگاه': {'target': 100, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        'بیشترین ضرر': {'target': 100, 'tolerance': 0, 'category': 'cost', 'method': 'step'},
        
        # === تصمیمات فوری ===
        'افزایش سرعت دستگاه': {'target': 100, 'tolerance': 0, 'category': 'efficiency', 'method': 'step'},
        'تعویض اپراتور': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        'تعمیر فوری': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        'اصلاح فرآیند': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        'اولویت اقدام اصلاحی': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        
        # === تحلیل نهایی ===
        'افزایش تولید (در صورت رفع گلوگاه)': {'target': 20, 'tolerance': 5, 'category': 'efficiency', 'method': 'sigmoid'},
        'افزایش سود (در صورت کاهش ضایعات)': {'target': 1000000, 'tolerance': 200000, 'category': 'cost', 'method': 'linear'},
        
        # === تأیید ===
        'سرپرست تولید': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        'مدیر کارخانه': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
        'تاریخ': {'target': 100, 'tolerance': 0, 'category': 'general', 'method': 'step'},
    }
    
    updated_count = 0
    not_found = []
    
    # دریافت همه آیتم‌ها
    items = Item.query.all()
    
    for item in items:
        if item.title in settings_map:
            setting = settings_map[item.title]
            item.target_value = setting['target']
            item.tolerance = setting['tolerance']
            item.category = setting['category']
            item.scoring_method = setting['method']
            updated_count += 1
        else:
            not_found.append(item.title)
    
    db.session.commit()
    
    flash(f'✅ {updated_count} آیتم با موفقیت به‌روزرسانی شدند.', 'success')
    if not_found:
        flash(f'⚠️ {len(not_found)} آیتم در دیکشنری تنظیمات پیدا نشد: {", ".join(not_found[:10])}', 'warning')
    
    return redirect(url_for('index'))

@app.route('/auto_tag_items')
@login_required
def auto_tag_items():
    """تگ‌گذاری خودکار آیتم‌ها بر اساس نام آن‌ها"""
    if current_user.role != 'admin':
        flash('فقط ادمین می‌تواند این کار را انجام دهد.', 'danger')
        return redirect(url_for('settings'))
    
    # ===== بررسی وجود فیلد tags در دیتابیس =====
    try:
        # یک کوئری ساده برای بررسی وجود ستون tags
        test_item = Item.query.first()
        if test_item:
            _ = test_item.tags  # اگر خطا ندهد، فیلد وجود دارد
    except Exception as e:
        flash(f'❌ فیلد tags در دیتابیس وجود ندارد. لطفاً ابتدا دیتابیس را به‌روزرسانی کنید. خطا: {str(e)}', 'danger')
        return redirect(url_for('settings'))
    
    tag_map = {
        'تولید واقعی': 'production_actual',
        'تولید برنامه': 'production_plan',
        'راندمان': 'efficiency',
        'OEE': 'oee',
        'مجموع توقف روزانه': 'stop_total',
        'کل ضایعات (ریال)': 'waste_total',
        'گلوگاه': 'bottleneck',
        'ضریب هماهنگی خط': 'line_balance',
        'درصد ضایعات کل': 'waste_total',
        'توقف': 'stop_total',
    }
    
    updated = 0
    already_tagged = 0
    not_found = []
    
    # دریافت همه آیتم‌ها
    all_items = Item.query.all()
    total_items = len(all_items)
    
    for title, tag in tag_map.items():
        items = Item.query.filter(Item.title.contains(title)).all()
        if not items:
            not_found.append(title)
            continue
        
        for item in items:
            existing = item.tags.split(',') if item.tags else []
            if tag not in existing:
                existing.append(tag)
                item.tags = ','.join(existing)
                updated += 1
            else:
                already_tagged += 1
    
    db.session.commit()
    
    # ===== پیام نتیجه =====
    msg_parts = []
    if updated > 0:
        msg_parts.append(f'✅ {updated} آیتم تگ‌گذاری شدند')
    if already_tagged > 0:
        msg_parts.append(f'ℹ️ {already_tagged} آیتم از قبل تگ داشتند')
    if not_found:
        msg_parts.append(f'⚠️ {len(not_found)} عنوان در دیکشنری پیدا نشد: {", ".join(not_found[:5])}')
    
    if msg_parts:
        flash(' | '.join(msg_parts), 'success' if updated > 0 else 'warning')
    else:
        flash('هیچ تغییری ایجاد نشد. ممکن است آیتم‌ها از قبل تگ‌گذاری شده باشند یا نام آن‌ها با لیست مطابقت نداشته باشد.', 'info')
    
    return redirect(url_for('settings'))

@app.route('/check_tags')
@login_required
def check_tags():
    """بررسی وضعیت تگ‌های آیتم‌ها"""
    if current_user.role != 'admin':
        flash('فقط ادمین دسترسی دارد.', 'danger')
        return redirect(url_for('settings'))
    
    items_with_tags = Item.query.filter(Item.tags != '').all()
    items_without_tags = Item.query.filter((Item.tags == '') | (Item.tags.is_(None))).all()
    
    result = {
        'total': Item.query.count(),
        'with_tags': len(items_with_tags),
        'without_tags': len(items_without_tags),
        'tagged_items': [(i.id, i.title, i.tags) for i in items_with_tags[:20]],
        'untagged_items': [(i.id, i.title) for i in items_without_tags[:20]]
    }
    
    return jsonify(result)


@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # اعتبارسنجی
        if not current_password or not new_password or not confirm_password:
            flash('❌ تمام فیلدها را پر کنید.', 'danger')
            return redirect(url_for('change_password'))
        
        if not current_user.check_password(current_password):
            flash('❌ رمز عبور فعلی اشتباه است.', 'danger')
            return redirect(url_for('change_password'))
        
        if len(new_password) < 6:
            flash('❌ رمز عبور جدید باید حداقل ۶ کاراکتر باشد.', 'danger')
            return redirect(url_for('change_password'))
        
        if new_password != confirm_password:
            flash('❌ رمز عبور جدید و تأیید آن مطابقت ندارند.', 'danger')
            return redirect(url_for('change_password'))
        
        # تغییر رمز
        current_user.set_password(new_password)
        db.session.commit()
        flash('✅ رمز عبور با موفقیت تغییر کرد.', 'success')
        return redirect(url_for('index'))
    
    return render_template('change_password.html')




if __name__ == '__main__':
    app.run(debug=True)

