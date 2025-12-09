
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime
import os
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from sqlalchemy import func, case

def parse_date(value):
    """Chuyển chuỗi ngày (yyyy-mm-dd) thành đối tượng date, nếu không hợp lệ thì trả None"""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///app.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
@app.context_processor
def inject_unread():
    if current_user.is_authenticated:
        cnt = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    else:
        cnt = 0
    return dict(unread_notif_count=cnt)


login_manager = LoginManager(app)
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

login_manager.login_view = "login"

# -------------------- Association Table (Task - User) --------------------
task_assignees = db.Table(
    "task_assignees",
    db.Column("task_id", db.Integer, db.ForeignKey("task.id"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
)

# -------------------- Models --------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(120), nullable=False)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

class Board(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    owner = db.relationship("User", backref="boards")

class List(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    position = db.Column(db.Integer, default=0)
    board_id = db.Column(db.Integer, db.ForeignKey("board.id"), nullable=False)
    board = db.relationship("Board", backref=db.backref("lists", cascade="all, delete-orphan", order_by="List.position"))

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)  # Task name
    description = db.Column(db.Text, default="")       # Remark
    position = db.Column(db.Integer, default=0)

    # New fields
    start_date = db.Column(db.Date, nullable=True)
    due_date = db.Column(db.Date, nullable=True)

    status = db.Column(db.String(20), default="In process")      # In process, Done, OverDue
    percentage = db.Column(db.Integer, default=0)                # 0,25,50,75,100
    priority = db.Column(db.String(10), default="Normal")        # Low, Normal, High, Urgent

    list_id = db.Column(db.Integer, db.ForeignKey("list.id"), nullable=False)
    list = db.relationship("List", backref=db.backref("tasks", cascade="all, delete-orphan", order_by="Task.position"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])


    # Many-to-many assignees
    assignees = db.relationship("User", secondary=task_assignees, backref="assigned_tasks")

# -------------------- Auth --------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        if not name or not email or not password:
            flash("Vui lòng điền đầy đủ thông tin.", "danger")
            return redirect(url_for("register"))
        if len(password) < 6:
            flash("Mật khẩu cần ít nhất 6 ký tự.", "danger")
            return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("Email đã tồn tại. Vui lòng dùng email khác.", "warning")
            return redirect(url_for("register"))
        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Đăng ký thành công! Bạn có thể đăng nhập.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Đăng nhập thành công!", "success")
            return redirect(url_for("dashboard"))
        flash("Email hoặc mật khẩu không đúng.", "danger")
        return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Đã đăng xuất.", "info")
    return redirect(url_for("index"))

# -------------------- Pages --------------------
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")

from sqlalchemy import func, case
from datetime import date, timedelta

@app.route("/dashboard")
@login_required
def dashboard():
    boards = Board.query.filter(
        (Board.owner_id == current_user.id) | (Board.owner_id.is_(None))
    ).all()

    today = date.today()

    # Ưu tiên trạng thái: OverDue -> In process -> Done
    STATUS_ORDER = {"OverDue": 0, "In process": 1, "Done": 2}

    q = Task.query.filter(Task.due_date.isnot(None))
    upcoming_all = q.all()

    # Sort theo: trạng thái ưu tiên + Due date
    upcoming_all.sort(
        key=lambda t: (
            STATUS_ORDER.get(t.status, 99),
            t.due_date or date.max,
        )
    )

    upcoming = upcoming_all[:8]


    # ==== tổng trạng thái ====
    total_inprocess = Task.query.filter_by(status="In process").count()
    total_done = Task.query.filter_by(status="Done").count()
    total_overdue = Task.query.filter_by(status="OverDue").count()

    # ==== thống kê theo user ====
    user_stats = (
        db.session.query(
            User.name.label("name"),
            func.count(Task.id).label("total"),
            func.sum(case((Task.status == "Done", 1), else_=0)).label("done"),
            func.sum(case((Task.status == "In process", 1), else_=0)).label("inprocess"),
            func.sum(case((Task.status == "OverDue", 1), else_=0)).label("overdue"),
        )
        .join(task_assignees, task_assignees.c.user_id == User.id)
        .join(Task, task_assignees.c.task_id == Task.id)
        .group_by(User.name)
        .order_by(func.count(Task.id).desc())
        .all()
    )

    total_assigned = sum(u.total for u in user_stats) if user_stats else 0
    total_done_all = sum(u.done for u in user_stats) if user_stats else 0
    percent_done = round((total_done_all / total_assigned * 100), 1) if total_assigned else 0
    percent_inprocess = round(100 - percent_done, 1) if total_assigned else 0

    # ==== So sánh theo tháng (6 tháng gần nhất) ====
    months = []
    month_done = []
    today = date.today()
    # tạo list từ 5 tháng trước đến tháng hiện tại
    for i in range(5, -1, -1):
        m_year = (today.year if today.month - i > 0 else today.year - 1)
        m_month = (today.month - i - 1) % 12 + 1
        # khoảng đầu-cuối tháng
        start_m = date(m_year, m_month, 1)
        if m_month == 12:
            end_m = date(m_year + 1, 1, 1)
        else:
            end_m = date(m_year, m_month + 1, 1)

        cnt_done = (
            Task.query.filter(
                Task.status == "Done",
                Task.due_date >= start_m,
                Task.due_date < end_m,
            ).count()
        )

        months.append(f"{m_month:02d}/{m_year}")
        month_done.append(cnt_done)

    # ==== Gantt: lấy ~10 task gần đây có start & due ====
    gantt_tasks = (
        Task.query
        .filter(Task.start_date.isnot(None), Task.due_date.isnot(None))
        .order_by(Task.due_date.desc())
        .limit(10)
        .all()
    )

    gantt_labels = []
    gantt_durations = []
    gantt_hints = []
    for t in gantt_tasks:
        days = (t.due_date - t.start_date).days
        if days < 0:
            days = 0
        gantt_labels.append(t.title)
        gantt_durations.append(days or 1)  # ít nhất 1 ngày cho dễ nhìn
        gantt_hints.append(f"{t.start_date} → {t.due_date}")

    return render_template(
        "dashboard.html",
        boards=boards,
        upcoming=upcoming,
        today=today,
        total_inprocess=total_inprocess,
        total_done=total_done,
        total_overdue=total_overdue,
        user_stats=user_stats,
        percent_done=percent_done,
        percent_inprocess=percent_inprocess,
        month_labels=months,
        month_done=month_done,
        gantt_labels=gantt_labels,
        gantt_durations=gantt_durations,
        gantt_hints=gantt_hints,
    )


@app.route("/boards", methods=["GET", "POST"], endpoint="boards_page")
@login_required
def boards_page():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        desc = request.form.get("description","").strip()
        if not name:
            flash("Tên bảng không được để trống.", "danger")
            return redirect(url_for("boards_page"))
        b = Board(name=name, description=desc, owner_id=current_user.id)
        db.session.add(b); db.session.commit()
        flash("Đã tạo board.", "success")
        return redirect(url_for("view_board", board_id=b.id))
    my_boards = Board.query.filter_by(owner_id=current_user.id).all()
    return render_template("boards.html", boards=my_boards)
# Alias để url_for('boards') cũng hoạt động
app.add_url_rule("/boards", endpoint="boards", view_func=boards_page, methods=["GET","POST"])

@app.route("/boards/<int:board_id>", methods=["GET", "POST"])
@login_required
def view_board(board_id):
    board = Board.query.get_or_404(board_id)

    # Thêm List mới
    if request.method == "POST":
        title = request.form.get("list_title","").strip()
        if title:
            pos = (board.lists[-1].position + 1) if board.lists else 1
            lst = List(title=title, board=board, position=pos)
            db.session.add(lst)
            db.session.commit()
            flash("Đã thêm danh sách.", "success")
        else:
            flash("Tên danh sách không được để trống.", "danger")
        return redirect(url_for("view_board", board_id=board.id))

    users = User.query.order_by(User.name.asc()).all()

    # ❌ Không còn build/passing summary ở đây
    return render_template("board_view.html", board=board, users=users)


@app.route("/lists/<int:list_id>/task", methods=["POST"])
@login_required
def add_task(list_id):
    lst = List.query.get_or_404(list_id)
    title = request.form.get("title","").strip()
    if not title:
        flash("Tên công việc không được để trống.", "danger")
        return redirect(url_for("view_board", board_id=lst.board_id))

    # fields
    description = request.form.get("description","").strip()
    start_date = parse_date(request.form.get("start_date"))
    due_date = parse_date(request.form.get("due_date"))

    status = request.form.get("status","In process")
    if status not in ["In process", "Done", "OverDue"]:
        status = "In process"

    try:
        percentage = int(request.form.get("percentage","0"))
    except ValueError:
        percentage = 0
    if percentage not in [0,25,50,75,100]:
        percentage = 0

    priority = request.form.get("priority","Normal")
    if priority not in ["Low", "Normal", "High", "Urgent"]:
        priority = "Normal"

    pos = (lst.tasks[-1].position + 1) if lst.tasks else 1

    t = Task(
    title=title,
    description=description,
    start_date=start_date,
    due_date=due_date,
    status=status,
    percentage=percentage,
    priority=priority,
    list=lst,
    position=pos,  # ✅ thêm dấu phẩy ở đây
    created_by_id=current_user.id,   # ✅ giờ cú pháp đúng
)

    assignee_ids = request.form.getlist("assignees")
    if assignee_ids:
        users = User.query.filter(User.id.in_(assignee_ids)).all()
    t.assignees = users
    for u in users:
        if u.id != current_user.id:
            notify(
                u, "assigned", t, current_user,
                f"{current_user.name} đã giao việc: “{t.title}”."
            )
    db.session.add(t)
    db.session.commit()

    flash("Đã thêm công việc.", "success")
    return redirect(url_for("view_board", board_id=lst.board_id))

# Helper gom dữ liệu cho Summary (nếu chưa có)
def build_summary_for_board(board_id: int):
    q = Task.query.join(List).filter(List.board_id == board_id)
    def _order(col):
        try:    return col.asc().nullslast()
        except: return col.asc()
    ip = q.filter(Task.status=="In process").order_by(_order(Task.due_date)).all()
    dn = q.filter(Task.status=="Done").order_by(_order(Task.due_date)).all()
    od = q.filter(Task.status=="OverDue").order_by(_order(Task.due_date)).all()
    return {"In process": ip, "Done": dn, "OverDue": od,
            "counts":{"In process":len(ip), "Done":len(dn), "OverDue":len(od)}}

# Trang Summary
@app.route("/boards/<int:board_id>/summary")
@login_required
def board_summary(board_id):
    board = Board.query.get_or_404(board_id)
    summary = build_summary_for_board(board.id)
    return render_template("board_summary.html", board=board, summary=summary)
@app.route("/summary")
@login_required
def all_summary():
    boards = Board.query.filter_by(owner_id=current_user.id).all()
    summaries = {}

    for b in boards:
        summaries[b.name] = build_summary_for_board(b.id)

    return render_template("all_summary.html", summaries=summaries)


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    t = Task.query.get_or_404(task_id)
    board_id = t.list.board_id
    db.session.delete(t)
    db.session.commit()
    flash("Đã xoá công việc.", "info")
    return redirect(url_for("view_board", board_id=board_id))
@app.route("/tasks/<int:task_id>/update", methods=["POST"])
@login_required
def update_task(task_id):
    t = Task.query.get_or_404(task_id)

    prev_status = t.status
    old_assignee_ids = {u.id for u in t.assignees}

    # cập nhật trường
    t.title = request.form.get("title", t.title).strip()
    t.description = request.form.get("description", t.description).strip()
    t.start_date = parse_date(request.form.get("start_date"))
    t.due_date = parse_date(request.form.get("due_date"))
    t.status = request.form.get("status", t.status)
    t.percentage = int(request.form.get("percentage", t.percentage) or 0)
    t.priority = request.form.get("priority", t.priority)

    # cập nhật assignees
    assignee_ids = request.form.getlist("assignees")
    if assignee_ids:
        users = User.query.filter(User.id.in_(assignee_ids)).all()
        t.assignees = users
    else:
        t.assignees = []

    # commit giá trị mới trước khi tạo noti (để dữ liệu chuẩn)
    db.session.commit()

    # 1) Người mới được thêm -> thông báo "assigned"
    new_ids = set(int(x) for x in (assignee_ids or []))
    added_ids = new_ids - old_assignee_ids
    if added_ids:
        for u in User.query.filter(User.id.in_(added_ids)).all():
            if u.id != current_user.id:
                notify(u, "assigned", t, current_user, f'{current_user.name} đã giao thêm cho bạn: "{t.title}".')
        db.session.commit()

    # 2) Task chuyển sang Done -> thông báo cho người giao và các assignees khác
    if prev_status != "Done" and t.status == "Done":
        receivers = set()
        if t.created_by_id and t.created_by_id != current_user.id:
            creator = User.query.get(t.created_by_id)
            if creator:
                receivers.add(creator)
        for u in t.assignees:
            if u.id != current_user.id:
                receivers.add(u)
        for u in receivers:
            notify(u, "completed", t, current_user, f'Task "{t.title}" đã hoàn thành.')
        db.session.commit()

    flash("Cập nhật task thành công!", "success")
    return redirect(url_for("view_board", board_id=t.list.board_id))




@app.route("/members")
@login_required
def members():
    users = User.query.order_by(User.name.asc()).all()
    return render_template("members.html", users=users)

@app.route("/calendar")
@login_required
def calendar():
    return render_template("calendar.html")

@app.route("/api/events")
@login_required
def events_api():
    events = []
    for t in Task.query.filter(Task.due_date.isnot(None)).all():
        events.append({
            "id": t.id,
            "title": f"{t.title} ({t.status})",
            "start": t.due_date.isoformat(),
            "url": url_for("view_board", board_id=t.list.board_id)
        })
    return jsonify(events)
@app.route("/chart")
@login_required
def chart():
    # Giới hạn dữ liệu theo các board do bạn sở hữu
    owner_id = current_user.id

    # Đếm task theo trạng thái
    status_counts_rows = (
        db.session.query(Task.status, func.count(Task.id))
        .join(List).join(Board)
        .filter(Board.owner_id == owner_id)
        .group_by(Task.status)
        .all()
    )
    status_counts = {"In process": 0, "Done": 0, "OverDue": 0}
    for st, cnt in status_counts_rows:
        if st in status_counts:
            status_counts[st] = cnt

    # Helper: top 10 assignees theo trạng thái
    def top_assignees(status):
        rows = (
            db.session.query(User.name, func.count(Task.id))
            .join(task_assignees, User.id == task_assignees.c.user_id)
            .join(Task, Task.id == task_assignees.c.task_id)
            .join(List).join(Board)
            .filter(Board.owner_id == owner_id, Task.status == status)
            .group_by(User.name)
            .order_by(func.count(Task.id).desc())
            .limit(10)
            .all()
        )
        labels = [r[0] for r in rows]
        values = [int(r[1]) for r in rows]
        return labels, values

    ip_labels, ip_values = top_assignees("In process")
    dn_labels, dn_values = top_assignees("Done")
    od_labels, od_values = top_assignees("OverDue")

    # Pie tiến độ trong nhóm In process (dựa trên percentage của từng task)
    inproc_tasks = (
        Task.query.join(List).join(Board)
        .filter(Board.owner_id == owner_id, Task.status == "In process")
        .all()
    )
    total_slots = 100 * len(inproc_tasks)
    completed_slots = sum(t.percentage or 0 for t in inproc_tasks)
    remaining_slots = max(total_slots - completed_slots, 0)

    return render_template(
        "chart.html",
        status_counts=status_counts,
        ip_labels=ip_labels, ip_values=ip_values,
        dn_labels=dn_labels, dn_values=dn_values,
        od_labels=od_labels, od_values=od_values,
        total_slots=total_slots,
        completed_slots=completed_slots,
        remaining_slots=remaining_slots
    )
class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True, nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=True)
    type = db.Column(db.String(20))          # 'assigned' | 'completed' | 'overdue'
    message = db.Column(db.String(300))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)  # người tạo ra sự kiện

    user = db.relationship("User", foreign_keys=[user_id])
    actor = db.relationship("User", foreign_keys=[actor_id])
    task = db.relationship("Task")
def notify(user: User, typ: str, task: Task | None, actor: User | None, message: str) -> None:
    n = Notification(
        user_id=user.id,
        task_id=task.id if task else None,
        type=typ,
        actor_id=actor.id if actor else None,
        message=message,
    )
    db.session.add(n)


@app.route("/notifications", methods=["GET", "POST"])
@login_required
def notifications():
    today = date.today()

    overdue_q = (
        Task.query.join(List).join(Board)
        .filter(
            Task.due_date.isnot(None),
            Task.due_date < today,
            Task.status != "Done",
        )
    )

    overdue_for_me = (
        overdue_q.join(task_assignees)
        .filter(task_assignees.c.user_id == current_user.id)
        .all()
    )
    overdue_by_me = overdue_q.filter(Task.created_by_id == current_user.id).all()

    # tạo noti overdue nếu chưa có
    for t in set(overdue_for_me + overdue_by_me):
        exists = (
            Notification.query
            .filter_by(user_id=current_user.id, task_id=t.id, type="overdue")  # <<=== dùng type
            .first()
        )
        if not exists:
            notify(current_user, "overdue", t, None, f"Task “{t.title}” đã quá hạn.")  # truyền "overdue"
    db.session.commit()

    # đánh dấu tất cả đã đọc
    if request.method == "POST" and request.form.get("mark_read") == "1":
        (Notification.query
         .filter_by(user_id=current_user.id, is_read=False)
         .update({Notification.is_read: True}))
        db.session.commit()
        flash("Đã đánh dấu tất cả là đã đọc.", "success")
        return redirect(url_for("notifications"))

    # sắp xếp: chưa đọc trước, mới nhất trước
    notifs = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.is_read.asc(), Notification.created_at.desc())
        .all()
    )
    return render_template("notifications.html", notifs=notifs)


@app.route("/notifications/<int:notif_id>/open")
@login_required
def notification_open(notif_id):
    n = (Notification.query
         .filter_by(id=notif_id, user_id=current_user.id)
         .first_or_404())
    if not n.is_read:
        n.is_read = True
        db.session.commit()

    task = Task.query.options(joinedload(Task.list)).get(n.task_id) if n.task_id else None
    if task and task.list:
        return redirect(url_for("view_board", board_id=task.list.board_id) + f"#task-{task.id}")
    flash("Task của thông báo này không còn tồn tại hoặc đã bị xoá.", "warning")
    return redirect(url_for("notifications"))


@app.route("/notifications/<int:notif_id>/delete", methods=["POST"])
@login_required
def notification_delete(notif_id):
    n = (Notification.query
         .filter_by(id=notif_id, user_id=current_user.id)
         .first_or_404())
    db.session.delete(n)
    db.session.commit()
    flash("Đã xoá thông báo.", "info")
    return redirect(url_for("notifications"))


@app.route("/my-tasks")
@login_required
def my_tasks():
    me_id = current_user.id

    # Tất cả task mà bạn được assign (kể cả tự assign)
    assigned = (
        Task.query
        .join(task_assignees)
        .filter(task_assignees.c.user_id == me_id)
        .order_by(Task.due_date.asc().nulls_last(), Task.id.desc())
        .all()
    )

    # Chia 2 nhóm: người khác giao cho mình / mình tự giao cho mình
    self_assigned = [t for t in assigned if (t.created_by_id == me_id)]
    from_others   = [t for t in assigned if (t.created_by_id != me_id)]

    return render_template(
        "my_tasks.html",
        from_others=from_others,
        self_assigned=self_assigned
    )
@app.route("/all_tasks")
@login_required
def all_tasks():
    assignee_filter = request.args.get("assignee", "").strip()
    status_filter = request.args.get("status", "").strip()
    name_filter = request.args.get("name", "").strip()
    due_filter = request.args.get("due", "").strip()

    query = Task.query.join(List).join(Board)

    # Lọc theo tên
    if name_filter:
        query = query.filter(Task.title.ilike(f"%{name_filter}%"))

    # Lọc theo trạng thái
    if status_filter:
        query = query.filter(Task.status == status_filter)

    # Lọc theo Assignees
    if assignee_filter:
        query = query.join(task_assignees).filter(task_assignees.c.user_id == int(assignee_filter))

    # Lọc theo ngày
    if due_filter:
        query = query.filter(Task.due_date == due_filter)

    tasks = query.order_by(Task.due_date.asc().nulls_last()).all()

    # Lấy danh sách users để lọc Assignees
    users = User.query.order_by(User.name.asc()).all()

    return render_template(
        "all_tasks.html",
        tasks=tasks,
        users=users,
        assignee_filter=assignee_filter,
        status_filter=status_filter,
        name_filter=name_filter,
        due_filter=due_filter,
    )

@app.route("/members/<int:user_id>/delete", methods=["POST"])
@login_required
def delete_member(user_id):
    # (tuỳ ý: kiểm tra quyền, ví dụ chỉ admin hoặc board owner)
    u = User.query.get_or_404(user_id)

    # Không cho xoá chính mình
    if u.id == current_user.id:
        flash("Bạn không thể xoá chính mình.", "warning")
        return redirect(url_for("members"))

    db.session.delete(u)
    db.session.commit()
    flash(f"Đã xoá thành viên {u.name}.", "success")
    return redirect(url_for("members"))  
# CLI helper to init db
@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Database initialized.")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)