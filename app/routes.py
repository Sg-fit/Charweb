from datetime import datetime, timezone
from urllib.parse import urlsplit
from flask import render_template, flash, redirect, url_for, request, abort, Response 
from flask_login import login_user, logout_user, current_user, login_required
from flask_babel import _
import sqlalchemy as sa
from app import app, db
from app.form import LoginForm, RegistrationForm, EditProfileForm, \
    EmptyForm, PostForm, ResetPasswordRequestForm, ResetPasswordForm
from app.models import User, Post, TrackedAction
from app.email import send_password_reset_email
from langdetect import detect, LangDetectException
import json

@app.before_request
def before_request():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.now(timezone.utc)
        db.session.commit()


@app.route('/', methods=['GET', 'POST'])
@app.route('/home', methods=['GET', 'POST'])
@login_required
def home():
    form = PostForm()
    if form.validate_on_submit():
        try:
            language = detect(form.post.data)
        except LangDetectException:
            language = ''
        post = Post(body=form.post.data, author=current_user, language=language)
        db.session.add(post)
        db.session.commit()
        flash(_('Your post is now live!'))
        return redirect(url_for('home'))
    page = request.args.get('page', 1, type=int)
    query = sa.select(Post).where(Post.user_id == current_user.id)
    query = query.order_by(Post.timestamp.desc())
    posts = db.paginate(query, page=page,
                        per_page=app.config['POSTS_PER_PAGE'], error_out=False)
    next_url = url_for('home', page=posts.next_num) \
        if posts.has_next else None
    prev_url = url_for('home', page=posts.prev_num) \
        if posts.has_prev else None
    return render_template('Main.html', title=_('Home'), form=form,
                           posts=posts.items, next_url=next_url,
                           prev_url=prev_url)


@app.route('/explore')
@login_required
def explore():
    page = request.args.get('page', 1, type=int)
    query = sa.select(Post).order_by(Post.timestamp.desc())
    posts = db.paginate(query, page=page,
                        per_page=app.config['POSTS_PER_PAGE'], error_out=False)
    next_url = url_for('explore', page=posts.next_num) \
        if posts.has_next else None
    prev_url = url_for('explore', page=posts.prev_num) \
        if posts.has_prev else None
    return render_template('Main.html', title=_('Explore'), posts=posts.items,
                           next_url=next_url, prev_url=prev_url)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            sa.select(User).where(User.username == form.username.data))
        if user is None or not user.check_password(form.password.data):
            flash(_('Invalid username or password'))
            return redirect(url_for('login'))
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or urlsplit(next_page).netloc != '':
            next_page = url_for('home')
        return redirect(next_page)
    return render_template('Login.html', title=_('Sign In'), form=form)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        if form.accept_terms.data:
            user.terms_accepted_at = datetime.now(timezone.utc)
        db.session.add(user)
        db.session.commit()
        flash(_('Congratulations, you are now a registered user!'))
        return redirect(url_for('login'))
    return render_template('register.html', title=_('Register'), form=form)


@app.route('/reset_password_request', methods=['GET', 'POST'])
def reset_password_request():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = ResetPasswordRequestForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            sa.select(User).where(User.email == form.email.data))
        if user:
            send_password_reset_email(user)
        flash(_('Check your email for the instructions to reset your password'))
        return redirect(url_for('login'))
    return render_template('reset_password_request.html',
                           title=_('Reset Password'), form=form)


@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    user = User.verify_reset_password_token(token)
    if not user:
        return redirect(url_for('home'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash(_('Your password has been reset.'))
        return redirect(url_for('login'))
    return render_template('reset_password_form.html', form=form)


@app.route('/user/<username>')
@login_required
def user(username):
    user = db.session.scalar(sa.select(User).where(User.username == username))
    if user is None:
        abort(404)
    page = request.args.get('page', 1, type=int)
    query = sa.select(Post).where(Post.user_id == user.id)
    query = query.order_by(Post.timestamp.desc())
    posts = db.paginate(query, page=page,
                        per_page=app.config['POSTS_PER_PAGE'],
                        error_out=False)
    next_url = url_for('user', username=user.username, page=posts.next_num) \
        if posts.has_next else None
    prev_url = url_for('user', username=user.username, page=posts.prev_num) \
        if posts.has_prev else None
    form = EmptyForm()
    return render_template('user.html', user=user, posts=posts.items,
                           next_url=next_url, prev_url=prev_url, form=form)


@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm(current_user.username)
    if form.validate_on_submit():
        current_user.username = form.username.data
        current_user.about_me = form.about_me.data
        db.session.commit()
        flash(_('Your changes have been saved.'))
        return redirect(url_for('edit_profile'))
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.about_me.data = current_user.about_me
    return render_template('edit_profile.html', title=_('Edit Profile'),
                           form=form)

@app.route('/api/track', methods=['POST'])
def track():
    actions = request.get_json(silent=True)
    if not actions or not isinstance(actions, list):
        return {'error': 'expected a JSON array of actions'}, 400

    user_id = current_user.id if current_user.is_authenticated else None

    for item in actions:
        action_type = item.get('type', 'unknown')
        target = item.get('target')
        details = {k: v for k, v in item.items()
                    if k not in ('type', 'target', 'timestamp')}
        client_ts = item.get('timestamp')
        if client_ts:
            try:
                parsed_ts = datetime.fromisoformat(client_ts.replace('Z', '+00:00'))
            except ValueError:
                parsed_ts = datetime.now(timezone.utc)
        else:
            parsed_ts = datetime.now(timezone.utc)
        record = TrackedAction(
            user_id=user_id,
            action_type=action_type,
            target=target,
            timestamp=parsed_ts,
            details=json.dumps(details)
        )
        db.session.add(record)        
    db.session.commit()
    return {'status': 'ok', 'stored': len(actions)}, 201


@app.route('/terms')
def terms():
    return render_template('terms.html', title=_('Terms of Service'),
    last_updated='June 2026')

@app.route('/admin/tracking')
@login_required
def admin_tracking():
    if not current_user.is_admin:
        abort(403)
    page = request.args.get('page', 1, type=int)
    user_filter = request.args.get('user', '', type=str)
    query = sa.select(TrackedAction).order_by(TrackedAction.timestamp.desc())
    if user_filter:
        query = query.join(User, TrackedAction.user_id == User.id, isouter=True) \
            .where(User.username == user_filter)
    actions = db.paginate(query, page=page, per_page=50, error_out=False)
    next_url = url_for('admin_tracking', page=actions.next_num, user=user_filter) \
        if actions.has_next else None
    prev_url = url_for('admin_tracking', page=actions.prev_num, user=user_filter) \
        if actions.has_prev else None
    return render_template('admin_tracking.html', title=_('Tracking Data'),
                            actions=actions.items, next_url=next_url,
                            prev_url=prev_url, user_filter=user_filter)

@app.route('/admin/tracking/export')
@login_required
def admin_tracking_export():
    if not current_user.is_admin:
        abort(403)
    user_filter = request.args.get('user', '', type=str)
    query = sa.select(TrackedAction).order_by(TrackedAction.timestamp.desc())
    if user_filter:
        query = query.join(User, TrackedAction.user_id == User.id, isouter=True) \
            .where(User.username == user_filter)
    all_actions = db.session.scalars(query).all()

    lines = ["timestamp,username,action_type,target,details"]
    for a in all_actions:
        username = a.user.username if a.user else "anonymous"
        details = (a.details or "").replace('"', '""')
        target = (a.target or "").replace('"', '""')
        lines.append(f'"{a.timestamp}","{username}","{a.action_type}","{target}","{details}"')
    csv_content = "\n".join(lines) + "\n"

    filename = f"tracking_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
# @app.route('/follow/<username>', methods=['POST'])
# @login_required
# def follow(username):
#     form = EmptyForm()
#     if form.validate_on_submit():
#         user = db.session.scalar(
#             sa.select(User).where(User.username == username))
#         if user is None:
#             flash(f'User {username} not found.')
#             return redirect(url_for('index'))
#         if user == current_user:
#             flash('You cannot follow yourself!')
#             return redirect(url_for('user', username=username))
#         current_user.follow(user)
#         db.session.commit()
#         flash(f'You are following {username}!')
#         return redirect(url_for('user', username=username))
#     else:
#         return redirect(url_for('index'))


# @app.route('/unfollow/<username>', methods=['POST'])
# @login_required
# def unfollow(username):
#     form = EmptyForm()
#     if form.validate_on_submit():
#         user = db.session.scalar(
#             sa.select(User).where(User.username == username))
#         if user is None:
#             flash(f'User {username} not found.')
#             return redirect(url_for('index'))
#         if user == current_user:
#             flash('You cannot unfollow yourself!')
#             return redirect(url_for('user', username=username))
#         current_user.unfollow(user)
#         db.session.commit()
#         flash(f'You are not following {username}.')
#         return redirect(url_for('user', username=username))
#     else:
#         return redirect(url_for('index'))