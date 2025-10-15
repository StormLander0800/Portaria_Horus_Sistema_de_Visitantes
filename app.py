
import os
import re
import csv
import io
import base64
from datetime import datetime, timedelta, time
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_from_directory
from flask_sqlalchemy import SQLAlchemy

APP_NAME = "Projeto HÓRUS – Visitantes & Alunos"
RETENTION_DAYS = 90
BASE_CLASS_TIME = os.environ.get("HORUS_BASE_CLASS_TIME", "07:30")  # HH:MM

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-horus")
db_path = os.path.join(os.path.dirname(__file__), "horus.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ---------- Models ----------
class Visitor(db.Model):
    __tablename__ = "visitors"
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    cpf = db.Column(db.String(20), nullable=True, unique=False)
    rg = db.Column(db.String(20), nullable=True, unique=False)
    company = db.Column(db.String(200), nullable=True)
    is_visitor = db.Column(db.Boolean, default=True)
    is_supplier = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class VisitEvent(db.Model):
    __tablename__ = "visit_events"
    id = db.Column(db.Integer, primary_key=True)
    visitor_id = db.Column(db.Integer, db.ForeignKey("visitors.id"), nullable=False)
    check_in = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    check_out = db.Column(db.DateTime, nullable=True)
    photo_path = db.Column(db.String(500), nullable=True)
    photo_taken_at = db.Column(db.DateTime, nullable=True)
    visitor = db.relationship("Visitor", backref=db.backref("visits", lazy=True))

class Student(db.Model):
    __tablename__ = "students"
    id = db.Column(db.Integer, primary_key=True)
    matricula = db.Column(db.String(50), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(200), nullable=False)
    class_name = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StudentArrival(db.Model):
    __tablename__ = "student_arrivals"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    arrived_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_late = db.Column(db.Boolean, default=False)
    student = db.relationship("Student", backref=db.backref("arrivals", lazy=True))

# ---------- Helpers ----------
def photos_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "photos")

def ensure_dirs():
    Path(photos_dir()).mkdir(parents=True, exist_ok=True)

def cleanup_expired_photos():
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    removed = 0
    pdir = Path(photos_dir())
    if pdir.exists():
        for p in pdir.glob("*.*"):
            try:
                mtime = datetime.utcfromtimestamp(p.stat().st_mtime)
                if mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                continue
    # Sanitize DB pointers
    for evt in VisitEvent.query.filter(VisitEvent.photo_path.isnot(None)).all():
        if not Path(evt.photo_path).exists():
            evt.photo_path = None
    if removed:
        db.session.commit()
    return removed

def normalize_doc(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\D+", "", s)

def find_or_create_visitor(full_name, cpf, rg, company, is_visitor, is_supplier):
    cpf_n = normalize_doc(cpf)
    rg_n = normalize_doc(rg)
    q = None
    if cpf_n:
        q = Visitor.query.filter(db.func.replace(Visitor.cpf, ".", "") == cpf_n)
    elif rg_n:
        q = Visitor.query.filter(db.func.replace(Visitor.rg, ".", "") == rg_n)
    v = q.first() if q is not None else None
    if not v:
        v = Visitor(
            full_name=full_name.strip(),
            cpf=cpf_n if cpf_n else None,
            rg=rg_n if rg_n else None,
            company=(company or "").strip() or None,
            is_visitor=bool(is_visitor),
            is_supplier=bool(is_supplier),
        )
        db.session.add(v)
    else:
        v.full_name = full_name.strip() or v.full_name
        if cpf_n: v.cpf = cpf_n
        if rg_n: v.rg = rg_n
        if company: v.company = company.strip()
        v.is_visitor = bool(is_visitor)
        v.is_supplier = bool(is_supplier)
    db.session.commit()
    return v

def save_photo_from_dataurl(visitor_id: int, data_url: str) -> str | None:
    if not data_url or not data_url.startswith("data:image"):
        return None
    header, b64data = data_url.split(",", 1)
    raw = base64.b64decode(b64data)
    ensure_dirs()
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    fname = f"vis_{visitor_id}_{ts}.png"
    file_path = os.path.join(photos_dir(), fname)
    with open(file_path, "wb") as f:
        f.write(raw)
    return file_path

def parse_base_class_time(today: datetime) -> datetime:
    try:
        hh, mm = [int(x) for x in BASE_CLASS_TIME.split(":")]
        return datetime.combine(today.date(), time(hour=hh, minute=mm))
    except Exception:
        return datetime.combine(today.date(), time(hour=7, minute=30))

def compute_is_late(arrived_at: datetime) -> bool:
    base_dt = parse_base_class_time(arrived_at)
    return arrived_at > base_dt

@app.before_request
def _maintenance():
    cleanup_expired_photos()

# ---------- Static photos route ----------
@app.route("/photos/<path:filename>")
def photo_file(filename):
    return send_from_directory(photos_dir(), filename)

# ---------- Home ----------
@app.route("/", methods=["GET"])
def home():
    open_vis_count = VisitEvent.query.filter(VisitEvent.check_out.is_(None)).count()
    today = datetime.utcnow().date()
    late_today = (
        db.session.query(StudentArrival)
        .filter(db.func.date(StudentArrival.arrived_at) == today)
        .filter(StudentArrival.is_late == True)
        .count()
    )
    return render_template("home.html", app_name=APP_NAME, open_vis_count=open_vis_count, late_today=late_today, base_class_time=BASE_CLASS_TIME)

# ---------- Visitors ----------
@app.route("/visit", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        cpf = request.form.get("cpf", "").strip()
        rg = request.form.get("rg", "").strip()
        company = request.form.get("company", "").strip()
        is_visitor = request.form.get("is_visitor") == "on"
        is_supplier = request.form.get("is_supplier") == "on"
        photo_data = request.form.get("photo_data", "")

        if not full_name:
            flash("Nome completo é obrigatório.", "error")
            return redirect(url_for("index"))
        if not (cpf or rg):
            flash("Informe CPF ou RG (pelo menos um).", "error")
            return redirect(url_for("index"))
        if not (is_visitor or is_supplier):
            flash("Selecione ao menos uma flag: Visitante e/ou Fornecedor.", "error")
            return redirect(url_for("index"))

        v = find_or_create_visitor(full_name, cpf, rg, company, is_visitor, is_supplier)

        evt = VisitEvent(visitor_id=v.id, check_in=datetime.utcnow())
        path = save_photo_from_dataurl(v.id, photo_data)
        if path:
            evt.photo_path = path
            evt.photo_taken_at = datetime.utcnow()

        db.session.add(evt)
        db.session.commit()
        flash("Entrada registrada com sucesso.", "success")
        return redirect(url_for("search", q=v.full_name))

    open_visits = (
        VisitEvent.query.filter(VisitEvent.check_out.is_(None))
        .order_by(VisitEvent.check_in.desc())
        .limit(10)
        .all()
    )
    return render_template("index.html", app_name=APP_NAME, open_visits=open_visits)

@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    results = []
    if q:
        like = f"%{q.lower()}%"
        results = (
            db.session.query(VisitEvent)
            .join(Visitor, VisitEvent.visitor_id == Visitor.id)
            .filter(
                db.or_(
                    db.func.lower(Visitor.full_name).like(like),
                    db.func.lower(db.func.ifnull(Visitor.cpf, "")).like(like),
                    db.func.lower(db.func.ifnull(Visitor.rg, "")).like(like),
                )
            )
            .order_by(VisitEvent.check_in.desc())
            .limit(200)
            .all()
        )
    else:
        results = (
            VisitEvent.query.order_by(VisitEvent.check_in.desc()).limit(100).all()
        )
    return render_template("search.html", app_name=APP_NAME, q=q, results=results)

@app.route("/checkout/<int:event_id>", methods=["POST"])
def checkout(event_id: int):
    evt = VisitEvent.query.get_or_404(event_id)
    if evt.check_out is None:
        evt.check_out = datetime.utcnow()
        db.session.commit()
        flash("Saída registrada.", "success")
    else:
        flash("Este registro já possui saída.", "warning")
    return redirect(request.referrer or url_for("search"))

@app.route("/new_entry/<int:visitor_id>", methods=["POST"])
def new_entry(visitor_id: int):
    v = Visitor.query.get_or_404(visitor_id)
    evt = VisitEvent(visitor_id=v.id, check_in=datetime.utcnow())
    db.session.add(evt)
    db.session.commit()
    flash("Nova entrada registrada.", "success")
    return redirect(url_for("search", q=v.full_name))

# ---------- Students ----------
@app.route("/students")
def students_home():
    students = Student.query.order_by(Student.full_name.asc()).limit(200).all()
    return render_template("students_home.html", app_name=APP_NAME, students=students)

@app.route("/students/new", methods=["GET", "POST"])
def students_new():
    if request.method == "POST":
        matricula = (request.form.get("matricula") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        class_name = (request.form.get("class_name") or "").strip()
        if not (matricula and full_name):
            flash("Matrícula e Nome são obrigatórios.", "error")
            return redirect(url_for("students_new"))

        s = Student.query.filter_by(matricula=matricula).first()
        if not s:
            s = Student(matricula=matricula, full_name=full_name, class_name=class_name)
            db.session.add(s)
        else:
            s.full_name = full_name
            s.class_name = class_name
        db.session.commit()
        flash("Aluno cadastrado/atualizado.", "success")
        return redirect(url_for("students_home"))
    return render_template("students_new.html", app_name=APP_NAME)

@app.route("/students/import", methods=["GET", "POST"])
def students_import():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Selecione um arquivo CSV.", "error")
            return redirect(url_for("students_import"))
        try:
            content = file.stream.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            # Normalize headers
            headers = { (h or "").strip().lower(): h for h in reader.fieldnames or [] }
            required = {"matricula", "nome_completo", "turma"}
            if not required.issubset(set(headers.keys())):
                flash("Cabeçalhos esperados: matricula, nome_completo, turma.", "error")
                return redirect(url_for("students_import"))
            created = updated = 0
            for row in reader:
                matricula = (row.get(headers["matricula"]) or "").strip()
                full_name = (row.get(headers["nome_completo"]) or "").strip()
                class_name = (row.get(headers["turma"]) or "").strip()
                if not (matricula and full_name):
                    continue
                s = Student.query.filter_by(matricula=matricula).first()
                if not s:
                    s = Student(matricula=matricula, full_name=full_name, class_name=class_name)
                    db.session.add(s)
                    created += 1
                else:
                    s.full_name = full_name
                    s.class_name = class_name
                    updated += 1
            db.session.commit()
            flash(f"Import concluído. Criados: {created} | Atualizados: {updated}.", "success")
        except Exception as e:
            flash(f"Erro no import: {e}", "error")
        return redirect(url_for("students_home"))
    return render_template("students_import.html", app_name=APP_NAME)

@app.route("/students/checkin", methods=["GET", "POST"])
def students_checkin():
    if request.method == "POST":
        q = (request.form.get("q") or "").strip()
        s = None
        if q:
            s = Student.query.filter(
                db.or_(Student.matricula == q, db.func.lower(Student.full_name) == q.lower())
            ).first()
        if not s:
            flash("Aluno não encontrado.", "error")
            return redirect(url_for("students_checkin"))
        now = datetime.utcnow()
        is_late = compute_is_late(now)
        arr = StudentArrival(student_id=s.id, arrived_at=now, is_late=is_late)
        db.session.add(arr)
        db.session.commit()
        flash(f"Chegada registrada: {'ATRASO' if is_late else 'no horário'} para {s.full_name}.", "success")
        return redirect(url_for("students_checkin"))
    recent = StudentArrival.query.order_by(StudentArrival.arrived_at.desc()).limit(20).all()
    return render_template("students_checkin.html", app_name=APP_NAME, recent=recent, base_class_time=BASE_CLASS_TIME)

# ---------- Reports ----------
@app.route("/reports", methods=["GET"])
def reports():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    def parse_date(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            return None
    d_from = parse_date(date_from) or (datetime.utcnow().date() - timedelta(days=7))
    d_to = parse_date(date_to) or datetime.utcnow().date()
    dt_from = datetime.combine(d_from, time.min)
    dt_to = datetime.combine(d_to, time.max)

    visits = (VisitEvent.query
              .filter(VisitEvent.check_in >= dt_from, VisitEvent.check_in <= dt_to)
              .order_by(VisitEvent.check_in.desc()).all())

    late_summary = (
        db.session.query(Student, db.func.count(StudentArrival.id))
        .join(StudentArrival, Student.id == StudentArrival.student_id)
        .filter(StudentArrival.arrived_at >= dt_from, StudentArrival.arrived_at <= dt_to)
        .filter(StudentArrival.is_late == True)
        .group_by(Student.id)
        .order_by(db.func.count(StudentArrival.id).desc(), Student.full_name.asc())
        .all()
    )
    return render_template("reports.html", app_name=APP_NAME, visits=visits, late_summary=late_summary, d_from=d_from, d_to=d_to)

@app.route("/reports/export/visits.csv")
def export_visits_csv():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    def parse_date(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            return None
    d_from = parse_date(date_from) or (datetime.utcnow().date() - timedelta(days=7))
    d_to = parse_date(date_to) or datetime.utcnow().date()
    dt_from = datetime.combine(d_from, time.min)
    dt_to = datetime.combine(d_to, time.max)

    rows = [["Nome", "Documento", "Tipo", "Entrada", "Saída"]]
    q = VisitEvent.query.filter(VisitEvent.check_in >= dt_from, VisitEvent.check_in <= dt_to).order_by(VisitEvent.check_in.asc())
    for e in q.all():
        doc = e.visitor.cpf or e.visitor.rg or ""
        tipo = "Visitante" if e.visitor.is_visitor else ""
        if e.visitor.is_supplier:
            tipo = (tipo + (" / " if tipo else "") + "Fornecedor")
        rows.append([e.visitor.full_name, doc, tipo, e.check_in.isoformat(sep=" "), e.check_out.isoformat(sep=" ") if e.check_out else ""])

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=visitas.csv"})

@app.route("/reports/export/late_students.csv")
def export_late_students_csv():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    def parse_date(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            return None
    d_from = parse_date(date_from) or (datetime.utcnow().date() - timedelta(days=30))
    d_to = parse_date(date_to) or datetime.utcnow().date()
    dt_from = datetime.combine(d_from, time.min)
    dt_to = datetime.combine(d_to, time.max)

    rows = [["Matrícula", "Nome", "Turma", "Atrasos (período)"]]
    late_q = (
        db.session.query(Student, db.func.count(StudentArrival.id))
        .join(StudentArrival, Student.id == StudentArrival.student_id)
        .filter(StudentArrival.arrived_at >= dt_from, StudentArrival.arrived_at <= dt_to)
        .filter(StudentArrival.is_late == True)
        .group_by(Student.id)
        .order_by(db.func.count(StudentArrival.id).desc(), Student.full_name.asc())
        .all()
    )
    for s, cnt in late_q:
        rows.append([s.matricula, s.full_name, s.class_name or "", cnt])

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=alunos_atrasos.csv"})

# ---------- CLI ----------
@app.cli.command("init-db")
def init_db():
    with app.app_context():
        db.create_all()
        ensure_dirs()
    print("Banco inicializado.")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_dirs()
    app.run(host="0.0.0.0", port=5000, debug=True)
