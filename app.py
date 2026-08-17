from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, Section, Item, Record, Operator, Supervisor, StopRecord, Responsibility, User
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime, date, timedelta
import json
import random
import jdatetime
from functools import wraps

# ========== ایجاد نمونه Flask ==========
app = Flask(__name__)

# ========== تنظیمات ==========
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///checklist.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your-secret-key-change-in-production'  # حتماً در محیط تولید تغییر دهید

# ========== مقداردهی اولیه db ==========
db.init_app(app)

# ========== مقداردهی اولیه افزونه‌ها ==========
bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'لطفاً ابتدا وارد شوید.'
login_manager.login_message_category = 'warning'

# ========== User Loader ==========
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ========== ایجاد دیتابیس و داده‌های اولیه ==========
with app.app_context():
    db.create_all()

    # ایجاد کاربر ادمین پیش‌فرض
    if User.query.count() == 0:
        admin = User(username='admin', full_name='مدیر سیستم')
        admin.set_password('123456')  # رمز پیش‌فرض: 123456
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
            sec = Section(name=name, description=desc, icon=icon)
            db.session.add(sec)
        db.session.commit()

        # ===== اضافه کردن آیتم‌های هر بخش =====
        # 1. مخازن و آماده‌سازی
        sec = Section.query.filter_by(name='مخازن و آماده‌سازی').first()
        items = [
            ('زمان آماده‌سازی هر بچ', 'number', 'دقیقه', '', True),
            ('تأخیر در شارژ مواد اولیه', 'number', 'دقیقه', '', False),
            ('دقت بریکس', 'number', '±0.2', '', True),
            ('دقت pH', 'number', '', '', False),
            ('زمان خواب محصول قبل از انتقال', 'number', 'دقیقه', '', False),
            ('تداخل بین بچ‌ها (وقفه بین تولید)', 'number', 'دقیقه', '', False),
            ('آماده بودن مخزن قبل از اتمام بچ قبلی', 'checkbox', '', '', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 2. انتقال به خط
        sec = Section.query.filter_by(name='انتقال به خط').first()
        items = [
            ('افت فشار در مسیر', 'number', 'بار', '', False),
            ('نشتی در خطوط', 'checkbox', '', '', False),
            ('هوا گرفتن سیستم', 'checkbox', '', '', False),
            ('سرعت انتقال مایع', 'number', 'لیتر/دقیقه', '', True),
            ('توقف به دلیل گرفتگی', 'number', 'دقیقه', '', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 3. دستگاه پرکن
        sec = Section.query.filter_by(name='دستگاه پرکن').first()
        items = [
            ('سرعت واقعی', 'number', 'بطری/دقیقه', '', True),
            ('اختلاف با ظرفیت اسمی', 'number', 'درصد', '', True),
            ('درصد بطری ناقص پر شده', 'number', 'درصد', '', True),
            ('نشتی نازل‌ها', 'checkbox', '', '', False),
            ('توقف برای تنظیم', 'number', 'دقیقه', '', False),
            ('عملکرد سنسورها', 'select', '', 'عالی,متوسط,ضعیف', False),
            ('یکنواختی حجم پرکنی', 'number', 'میلی‌لیتر', '', False),
            ('راندمان پرکن', 'number', 'درصد', '', True),
            ('ضایعات پرکن', 'number', 'درصد', '', True),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 4. درب‌بند
        sec = Section.query.filter_by(name='درب‌بند').first()
        items = [
            ('شل بودن درب', 'number', 'درصد', '', False),
            ('بیش از حد سفت شدن', 'number', 'درصد', '', False),
            ('گیر کردن درب‌ها', 'number', 'دقیقه', '', False),
            ('توقف به دلیل تغذیه درب', 'number', 'دقیقه', '', False),
            ('هماهنگی با پرکن', 'select', '', 'عالی,متوسط,ضعیف', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 5. لیبل‌زن
        sec = Section.query.filter_by(name='لیبل‌زن').first()
        items = [
            ('کج شدن لیبل', 'number', 'درصد', '', True),
            ('نچسبیدن کامل', 'number', 'درصد', '', True),
            ('توقف به دلیل رول لیبل', 'number', 'دقیقه', '', False),
            ('سرعت کمتر از خط', 'number', 'درصد', '', False),
            ('مصرف بیش از حد لیبل', 'number', 'درصد', '', False),
            ('درصد خطای لیبل', 'number', 'درصد', '', True),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 6. شرینک پک
        sec = Section.query.filter_by(name='شرینک پک').first()
        items = [
            ('دمای تونل', 'number', 'درجه سانتی‌گراد', '', False),
            ('چروک یا پارگی نایلون', 'number', 'درصد', '', False),
            ('توقف به دلیل گیر کردن پک', 'number', 'دقیقه', '', False),
            ('مصرف نایلون', 'number', 'کیلوگرم', '', False),
            ('سرعت خروجی', 'number', 'پک/دقیقه', '', True),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 7. نوار نقاله‌ها
        sec = Section.query.filter_by(name='نوار نقاله‌ها').first()
        items = [
            ('تجمع بطری (Backlog)', 'number', 'عدد', '', False),
            ('فاصله غیر استاندارد بطری‌ها', 'number', 'درصد', '', False),
            ('توقف به دلیل گیر کردن', 'number', 'دقیقه', '', False),
            ('سرعت نامتوازن بین دستگاه‌ها', 'number', 'درصد', '', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 8. گلوگاه خط
        sec = Section.query.filter_by(name='گلوگاه خط').first()
        items = [
            ('کندترین دستگاه خط', 'text', '', '', False),
            ('اختلاف سرعت با سایر دستگاه‌ها', 'number', 'درصد', '', False),
            ('آیا سایر دستگاه‌ها منتظر آن می‌مانند؟', 'checkbox', '', '', False),
            ('چند بار در شیفت باعث توقف شده؟', 'number', 'بار', '', False),
            ('نتیجه گلوگاه', 'text', '', '', True),
            ('نام دستگاه گلوگاه (دقیق)', 'text', '', '', False),
            ('تعداد توقف‌های بحرانی', 'number', 'بار', '', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 9. تحلیل توقفات
        sec = Section.query.filter_by(name='تحلیل توقفات').first()
        items = [
            ('مجموع توقف روزانه', 'number', 'دقیقه', '', True),
            ('تعداد توقف‌ها', 'number', 'عدد', '', False),
            ('بیشترین دلیل توقف: دستگاه', 'number', 'دقیقه', '', False),
            ('بیشترین دلیل توقف: مواد', 'number', 'دقیقه', '', False),
            ('بیشترین دلیل توقف: اپراتور', 'number', 'دقیقه', '', False),
            ('زمان هر توقف', 'number', 'دقیقه', '', False),
            ('میانگین زمان توقف', 'number', 'دقیقه', '', True),
            ('تعداد توقف‌های برنامه‌ریزی‌نشده', 'number', 'بار', '', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 10. ضایعات
        sec = Section.query.filter_by(name='ضایعات').first()
        items = [
            ('بطری خراب', 'number', 'عدد', '', False),
            ('نشتی', 'number', 'عدد', '', False),
            ('لیبل خراب', 'number', 'عدد', '', False),
            ('برگشتی خط', 'number', 'عدد', '', False),
            ('کل ضایعات (ریال)', 'number', 'ریال', '', True),
            ('درصد ضایعات کل', 'number', 'درصد', '', True),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 11. اپراتور و شیفت
        sec = Section.query.filter_by(name='اپراتور و شیفت').first()
        items = [
            ('بهترین شیفت (مقایسه)', 'text', '', '', False),
            ('ضعیف‌ترین اپراتور', 'text', '', '', False),
            ('زمان واکنش به خطا', 'number', 'دقیقه', '', False),
            ('وابستگی خط به اپراتور خاص', 'checkbox', '', '', False),
            ('امتیاز عملکرد اپراتور برتر', 'number', 'امتیاز', '', True),
            ('تعداد خطاهای انسانی', 'number', 'بار', '', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 12. هماهنگی خط
        sec = Section.query.filter_by(name='هماهنگی خط').first()
        items = [
            ('آیا سرعت همه دستگاه‌ها یکی است؟', 'checkbox', '', '', False),
            ('آیا دستگاهی همیشه بیکار می‌ماند؟', 'checkbox', '', '', False),
            ('آیا دستگاهی همیشه پر فشار است؟', 'checkbox', '', '', False),
            ('ضریب هماهنگی خط', 'number', 'درصد', '', True),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 13. گزارش طلایی
        sec = Section.query.filter_by(name='گزارش طلایی').first()
        items = [
            ('تولید برنامه', 'number', 'بطری', '', True),
            ('تولید واقعی', 'number', 'بطری', '', True),
            ('راندمان', 'number', 'درصد', '', True),
            ('توقف', 'number', 'دقیقه', '', True),
            ('ضایعات', 'number', 'درصد', '', True),
            ('گلوگاه', 'text', '', '', True),
            ('بیشترین ضرر', 'text', '', '', True),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 14. تصمیمات فوری
        sec = Section.query.filter_by(name='تصمیمات فوری').first()
        items = [
            ('افزایش سرعت دستگاه', 'text', '', '', False),
            ('تعویض اپراتور', 'text', '', '', False),
            ('تعمیر فوری', 'text', '', '', False),
            ('اصلاح فرآیند', 'text', '', '', False),
            ('اولویت اقدام اصلاحی', 'select', '', 'بالا,متوسط,پایین', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 15. تحلیل نهایی
        sec = Section.query.filter_by(name='تحلیل نهایی').first()
        items = [
            ('افزایش تولید (در صورت رفع گلوگاه)', 'number', 'درصد', '', True),
            ('افزایش سود (در صورت کاهش ضایعات)', 'number', 'تومان', '', True),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
            db.session.add(item)

        # 16. تأیید
        sec = Section.query.filter_by(name='تأیید').first()
        items = [
            ('سرپرست تولید', 'text', '', '', False),
            ('مدیر کارخانه', 'text', '', '', False),
            ('تاریخ', 'text', '', '', False),
        ]
        for title, ftype, unit, opts, kpi in items:
            item = Item(section_id=sec.id, title=title, field_type=ftype, unit=unit, options=opts, is_kpi=kpi)
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

# ========== فیلتر تاریخ شمسی ==========
@app.template_filter('jalali')
def to_jalali(date_obj):
    if date_obj:
        return jdatetime.date.fromgregorian(date=date_obj).strftime('%Y/%m/%d')
    return ''

# ========== صفحات احراز هویت ==========
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

# ========== دکوراتور نقش کاربری ==========
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

# ========== مسیرهای اصلی ==========

@app.route('/')
@login_required
def index():
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    summary = {}
    recent_stops = []
    shift_performance = []
    
    if last_date:
        records = Record.query.filter(Record.shift_date == last_date).all()
        for rec in records:
            if rec.item.is_kpi:
                try:
                    summary[rec.item.title] = float(rec.value)
                except:
                    summary[rec.item.title] = rec.value
        
        recent_stops = StopRecord.query.order_by(StopRecord.created_at.desc()).limit(5).all()
        
        shifts = ['صبح', 'عصر', 'شب']
        for shift in shifts:
            shift_records = Record.query.filter_by(shift_date=last_date, shift_name=shift).all()
            prod = 0
            eff = 0
            stp = 0
            wst = 0
            for rec in shift_records:
                if 'تولید واقعی' in rec.item.title:
                    try: prod = float(rec.value)
                    except: pass
                if 'راندمان' in rec.item.title:
                    try: eff = float(rec.value)
                    except: pass
                if 'مجموع توقف روزانه' in rec.item.title:
                    try: stp = float(rec.value)
                    except: pass
                if 'کل ضایعات (ریال)' in rec.item.title:
                    try: wst = float(rec.value)
                    except: pass
            score = round(eff - (stp * 0.5) - (wst / 1000), 2)
            shift_performance.append({
                'name': shift,
                'production': prod,
                'efficiency': eff,
                'stops': stp,
                'waste': wst,
                'score': score
            })
    
    return render_template('index.html',
                           summary=summary,
                           recent_stops=recent_stops,
                           shift_performance=shift_performance,
                           last_date=last_date)

@app.route('/record/new', methods=['GET', 'POST'])
@login_required
def add_record():
    operators = Operator.query.all()
    if request.method == 'POST':
        shift_date = request.form.get('shift_date')
        shift_name = request.form.get('shift_name')
        operator_id = request.form.get('operator_id')
        
        if not shift_date:
            flash('تاریخ را وارد کنید.', 'danger')
            return redirect(url_for('add_record'))
        
        items = Item.query.all()
        for item in items:
            value = request.form.get(f'item_{item.id}')
            if value is None:
                continue
            if item.field_type == 'checkbox':
                value = '1' if value == 'on' else '0'
            record = Record(
                item_id=item.id,
                shift_date=datetime.strptime(shift_date, '%Y-%m-%d').date(),
                shift_name=shift_name,
                operator_id=int(operator_id) if operator_id else None,
                value=value
            )
            db.session.add(record)
        db.session.commit()
        flash('رکورد با موفقیت ثبت شد', 'success')
        return redirect(url_for('records_list'))
    
    sections = Section.query.all()
    total_items = sum(len(section.items) for section in sections)
    today = date.today().isoformat()
    return render_template('add_record.html', sections=sections, today=today, operators=operators, total_items=total_items)

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
    try:
        plan = float(report_data.get('تولید برنامه', 1))
        actual = float(report_data.get('تولید واقعی', 0))
        efficiency = (actual / plan * 100) if plan else 0
        stops = int(report_data.get('مجموع توقف روزانه', 0))
        waste = float(report_data.get('کل ضایعات (ریال)', 0))
        bottleneck = report_data.get('گلوگاه', 'نامشخص')
    except:
        efficiency = 0
        stops = 0
        waste = 0
        bottleneck = 'نامشخص'
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

# ========== مدیریت اپراتورها ==========

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

# ========== مدیریت مسئولین ==========
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

# ========== مدیریت انتساب بخش‌ها به افراد ==========
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

# ========== توابع ارزیابی ==========

def calculate_person_performance(person, sections, person_type):
    total_score = 0
    total_weight = 0
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    
    if not last_date:
        return {
            'person': person,
            'type': person_type,
            'score': 0,
            'sections_count': len(sections),
            'details': []
        }
    
    details = []
    
    for section in sections:
        items = Item.query.filter_by(section_id=section.id).all()
        for item in items:
            rec = Record.query.filter_by(item_id=item.id, shift_date=last_date).first()
            if rec:
                try:
                    value = float(rec.value)
                    weight = item.weight or 1.0
                    impact = item.impact_type or 'positive'
                    
                    normalized_value = min(max(value, 0), 100)
                    
                    if impact == 'positive':
                        item_score = normalized_value * weight
                    else:
                        item_score = (100 - normalized_value) * weight
                    
                    total_score += item_score
                    total_weight += weight
                    
                    details.append({
                        'item_title': item.title,
                        'value': value,
                        'weight': weight,
                        'impact': impact,
                        'item_score': item_score
                    })
                except:
                    pass
    
    final_score = total_score / total_weight if total_weight > 0 else 0
    
    return {
        'person': person,
        'type': person_type,
        'score': round(final_score, 2),
        'sections_count': len(sections),
        'details': details
    }

def calculate_overall_performance(sections):
    total_efficiency = 0
    total_stops = 0
    total_waste = 0
    count = 0
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    
    if not last_date:
        return {'efficiency': 0, 'stops': 0, 'waste': 0, 'score': 0, 'details': []}
    
    details = []
    
    for section in sections:
        items = Item.query.filter_by(section_id=section.id).all()
        for item in items:
            rec = Record.query.filter_by(item_id=item.id, shift_date=last_date).first()
            if rec:
                try:
                    value = float(rec.value)
                    weight = item.weight or 1.0
                    impact = item.impact_type or 'positive'
                    
                    normalized_value = min(max(value, 0), 100)
                    
                    if impact == 'positive':
                        item_score = normalized_value * weight
                    else:
                        item_score = (100 - normalized_value) * weight
                    
                    # جمع‌آوری مقادیر برای محاسبه میانگین
                    if 'راندمان' in item.title:
                        total_efficiency += value
                        count += 1
                    elif 'مجموع توقف' in item.title:
                        total_stops += value
                    elif 'کل ضایعات' in item.title:
                        total_waste += value
                    
                    details.append({
                        'item_title': item.title,
                        'value': value,
                        'weight': weight,
                        'impact': impact,
                        'item_score': item_score
                    })
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

# ========== تنظیم وزن آیتم‌ها ==========
@app.route('/item_weights', methods=['GET', 'POST'])
@login_required
def item_weights():
    if request.method == 'POST':
        item_id = request.form.get('item_id')
        weight = request.form.get('weight')
        impact_type = request.form.get('impact_type')
        
        item = Item.query.get(item_id)
        if item:
            item.weight = float(weight)
            item.impact_type = impact_type
            db.session.commit()
            flash('وزن آیتم با موفقیت به‌روزرسانی شد', 'success')
        return redirect(url_for('item_weights'))
    
    items = Item.query.all()
    sections = Section.query.all()
    return render_template('item_weights.html', items=items, sections=sections)

# ========== گزارش هوشمند ==========
@app.route('/smart_report')
@login_required
def smart_report():
    last_date = db.session.query(db.func.max(Record.shift_date)).scalar()
    if not last_date:
        flash('هنوز داده‌ای برای تحلیل وجود ندارد', 'warning')
        return redirect(url_for('index'))
    
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
        analysis['oee'] = float(report_data.get('OEE', 0)) if report_data.get('OEE') else 0
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
        availability = 1 - (analysis['stops'] / 480)
        performance = analysis['efficiency'] / 100
        quality = 1 - (analysis['waste'] / (analysis['production_actual'] * 1000)) if analysis['production_actual'] > 0 else 0.9
        analysis['oee'] = availability * performance * quality * 100
        analysis['oee'] = min(max(analysis['oee'], 0), 100)
    
    trends = []
    for i in range(7, 0, -1):
        d = last_date - timedelta(days=i)
        recs = Record.query.filter(Record.shift_date == d).all()
        if recs:
            data = {r.item.title: r.value for r in recs}
            eff = float(data.get('راندمان', 0)) if data.get('راندمان') else 0
            trends.append({'date': d, 'efficiency': eff})
    
    return render_template('smart_report.html',
                           analysis=analysis,
                           recommendations=recommendations,
                           trends=trends,
                           record_date=last_date)

# ========== تنظیمات ==========
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
    return render_template('settings.html', sections=sections, items=items)

# ========== گزارش نهایی ==========
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

# ========== API ==========
@app.route('/api/oee_data')
@login_required
def oee_data():
    end_date = date.today()
    start_date = end_date - timedelta(days=7)
    dates = []
    oee_values = []
    for d in (start_date + timedelta(n) for n in range(8)):
        dates.append(d.isoformat())
        oee_values.append(round(random.uniform(60, 90), 1))
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

# ========== راه‌اندازی ==========
if __name__ == '__main__':
    app.run(debug=True)