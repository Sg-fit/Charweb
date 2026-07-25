from datetime import datetime, timezone
from urllib.parse import urlsplit, urlparse
from flask import render_template, flash, redirect, url_for, request, abort, Response, current_app, jsonify, session
from flask_login import login_user, logout_user, current_user, login_required
from flask_babel import _
import sqlalchemy as sa
from app import app, db
from app import ai_defense
from app.form import LoginForm, RegistrationForm, EditProfileForm, \
    EmptyForm, PostForm, ResetPasswordRequestForm, ResetPasswordForm
# Correct import for all models
from app.models import User, Post, Comment, Like, TrackedAction, UserSession, Message, Notification, DailySignIn, PlayerCharacter, Equipment, PlayerInventory

from app.email import send_password_reset_email
from langdetect import detect, LangDetectException
import json
import os
import secrets
from werkzeug.utils import secure_filename


@app.before_request
def before_request():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.now(timezone.utc)
        db.session.commit()


def _is_safe_next_url(target):
    if not target:
        return False
    normalized = target.replace('\\', '/')
    parsed = urlparse(normalized)
    return parsed.scheme == '' and parsed.netloc == '' and normalized.startswith('/')


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'gif'})


@app.route('/', methods=['GET', 'POST'])
@app.route('/home', methods=['GET', 'POST'])
@login_required
def home():
    form = PostForm()
    if form.validate_on_submit():
        image_filename = None
        if form.image.data and allowed_file(form.image.data.filename):
            filename = secure_filename(form.image.data.filename)
            image_filename = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{filename}"
            form.image.data.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))

        try:
            language = detect(form.post.data)
        except LangDetectException:
            language = 'en'

        post = Post(body=form.post.data, author=current_user, language=language, image_filename=image_filename)
        db.session.add(post)
        db.session.commit()
        flash(_('Your post is now live!'))
        return redirect(url_for('home'))

    page = request.args.get('page', 1, type=int)
    query = sa.select(Post).order_by(Post.timestamp.desc())
    posts = db.paginate(query, page=page,
                        per_page=app.config['POSTS_PER_PAGE'], error_out=False)

    next_url = url_for('home', page=posts.next_num) if posts.has_next else None
    prev_url = url_for('home', page=posts.prev_num) if posts.has_prev else None

    return render_template('Main.html', 
                           title=_('Home'), 
                           form=form,
                           posts=posts.items, 
                           next_url=next_url,
                           prev_url=prev_url)

@app.route('/comment/<int:post_id>', methods=['POST'])
@login_required
def comment(post_id):
    post = db.session.get(Post, post_id)
    if not post:
        abort(404)
    
    body = request.form.get('body', '').strip()
    if body and len(body) <= 500:
        comment = Comment(body=body, author=current_user, post=post)
        db.session.add(comment)
        db.session.commit()
        flash(_('Your comment has been posted.'))
    return redirect(url_for('home'))


@app.route('/like/<int:post_id>', methods=['POST'])
@login_required
def like(post_id):
    post = db.session.get(Post, post_id)
    if not post:
        return jsonify({'error': 'Post not found'}), 404

    like = db.session.scalar(
        sa.select(Like).where(
            Like.user_id == current_user.id,
            Like.post_id == post_id
        )
    )

    if like:
        db.session.delete(like)
        liked = False
    else:
        like = Like(user_id=current_user.id, post_id=post_id)
        db.session.add(like)
        liked = True

    db.session.commit()
    return jsonify({
        'liked': liked, 
        'count': len(post.likes)
    })

# Keep all your other routes (explore, login, register, etc.) as they are...
# (I didn't repeat them here to save space, but keep them unchanged)

@app.route('/explore')
@login_required
def explore():
    page = request.args.get('page', 1, type=int)
    query = sa.select(Post).order_by(Post.timestamp.desc())
    posts = db.paginate(query, page=page,
                        per_page=app.config['POSTS_PER_PAGE'], error_out=False)
    next_url = url_for('explore', page=posts.next_num) if posts.has_next else None
    prev_url = url_for('explore', page=posts.prev_num) if posts.has_prev else None
    form = PostForm()
    return render_template('Main.html', title=_('Explore'), posts=posts.items,
                           next_url=next_url, prev_url=prev_url, form=form)


# ... rest of your routes (login, register, user, edit_profile, track, terms, admin, etc.)

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
        if not _is_safe_next_url(next_page):
            next_page = url_for('home')
        return redirect(next_page)

    # Fresh quick-login links for the standing test accounts, for agents
    # that can only navigate (GET) and can't submit this form (POST).
    quick_login_links = {}
    for uname in ('ai_test_shared', 'human_test_shared'):
        test_user = db.session.scalar(sa.select(User).where(User.username == uname))
        if test_user:
            quick_login_links[uname] = url_for(
                'quick_login', token=test_user.get_login_token(), _external=True)

    return render_template('Login.html', title=_('Sign In'), form=form,
                            quick_login_links=quick_login_links)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/login/quick/<token>')
def quick_login(token):
    """GET-only login for agents/harnesses that can't submit a POST form
    (e.g. LLM-driven browsing tools restricted to navigation). Token is a
    signed, expiring JWT from User.get_login_token() -- not a bare
    username/password, so it can't be guessed or reused for other accounts."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    user = User.verify_login_token(token)
    if not user:
        flash(_('That login link is invalid or has expired.'))
        return redirect(url_for('login'))
    login_user(user)
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

    # UA doesn't change mid-session, so it's captured once per session/user
    # record here rather than duplicated onto every tracked event.
    if 'session_uid' not in session:
        session['session_uid'] = secrets.token_hex(16)
    session_uid = session['session_uid']

    ua_string = request.headers.get('User-Agent', '')
    accept_lang = request.headers.get('Accept-Language', '')
    is_bot_ua, ua_bot_reason = ai_defense.classify_user_agent(ua_string)

    now = datetime.now(timezone.utc)
    user_session = db.session.scalar(
        sa.select(UserSession).where(UserSession.session_uid == session_uid))
    if user_session is None:
        user_session = UserSession(session_uid=session_uid, first_seen=now)
        db.session.add(user_session)
    user_session.user_id = user_id
    user_session.user_agent = ua_string
    user_session.accept_language = accept_lang
    user_session.ua_bot_flag = is_bot_ua
    user_session.ua_bot_reason = ua_bot_reason
    user_session.last_seen = now

    if current_user.is_authenticated:
        current_user.last_user_agent = ua_string
        current_user.last_accept_language = accept_lang

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
            session_uid=session_uid,
            action_type=action_type,
            target=target,
            timestamp=parsed_ts,
            details=json.dumps(details)
        )
        db.session.add(record)
    db.session.commit()

    prediction, probability = ai_defense.score_session(session_uid)
    if prediction is not None:
        user_session.ai_prediction = prediction
        user_session.ai_probability = probability
        user_session.last_scored_at = datetime.now(timezone.utc)
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
    show_mousemove = request.args.get('show_mousemove', '', type=str) == '1'
    query = sa.select(TrackedAction).order_by(TrackedAction.timestamp.desc())
    if user_filter:
        query = query.join(User, TrackedAction.user_id == User.id, isouter=True) \
            .where(User.username.ilike(f'%{user_filter}%'))
    if not show_mousemove:
        query = query.where(TrackedAction.action_type != 'mousemove')
    actions = db.paginate(query, page=page, per_page=50, error_out=False)

    session_uids = {a.session_uid for a in actions.items if a.session_uid}
    sessions_by_uid = {}
    if session_uids:
        sessions = db.session.scalars(
            sa.select(UserSession).where(UserSession.session_uid.in_(session_uids))
        ).all()
        sessions_by_uid = {s.session_uid: s for s in sessions}

    next_url = url_for('admin_tracking', page=actions.next_num, user=user_filter,
                        show_mousemove='1' if show_mousemove else None) \
        if actions.has_next else None
    prev_url = url_for('admin_tracking', page=actions.prev_num, user=user_filter,
                        show_mousemove='1' if show_mousemove else None) \
        if actions.has_prev else None
    return render_template('admin_tracking.html', title=_('Tracking Data'),
                            actions=actions.items, next_url=next_url,
                            prev_url=prev_url, user_filter=user_filter,
                            show_mousemove=show_mousemove,
                            sessions_by_uid=sessions_by_uid)

@app.route('/admin/tracking/export')
@login_required
def admin_tracking_export():
    if not current_user.is_admin:
        abort(403)
    user_filter = request.args.get('user', '', type=str)
    query = sa.select(TrackedAction).order_by(TrackedAction.timestamp.desc())
    if user_filter:
        query = query.join(User, TrackedAction.user_id == User.id, isouter=True) \
            .where(User.username.ilike(f'%{user_filter}%'))
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

@app.route('/api/notifications')
@login_required
def get_notifications():
    notifs = db.session.scalars(
        sa.select(Notification)
        .where(Notification.user_id == current_user.id)
        .where(Notification.is_read == False)
        .order_by(Notification.timestamp.desc())
    ).all()
    return jsonify([{'id': n.id, 'message': n.message, 'timestamp': n.timestamp.isoformat()} for n in notifs])

@app.route('/api/notifications/read', methods=['POST'])
@login_required
def mark_notifications_read():
    db.session.execute(
        sa.update(Notification)
        .where(Notification.user_id == current_user.id)
        .values(is_read=True)
    )
    db.session.commit()
    return {'status': 'ok'}


@app.route('/daily', methods=['GET'])
@login_required
def daily():
    from datetime import date
    today = date.today()
    already_signed = db.session.scalar(
        sa.select(DailySignIn).where(
            DailySignIn.user_id == current_user.id,
            sa.func.date(DailySignIn.date) == today
        )
    )
    char = db.session.scalar(sa.select(PlayerCharacter).where(PlayerCharacter.user_id == current_user.id))
    if char and char.is_dead and char.death_date:
        if char.death_date.date() < today:
            char.is_dead = False
            char.hp = char.max_hp
            db.session.commit()
    return render_template('daily.html', already_signed=already_signed, char=char,
                           tokens=current_user.tokens)

@app.route('/daily/signin', methods=['POST'])
@login_required
def daily_signin():
    from datetime import date
    today = date.today()
    already_signed = db.session.scalar(
        sa.select(DailySignIn).where(
            DailySignIn.user_id == current_user.id,
            sa.func.date(DailySignIn.date) == today
        )
    )
    if not already_signed:
        signin = DailySignIn(user_id=current_user.id, tokens_earned=10)
        db.session.add(signin)
        current_user.tokens += 50
        db.session.commit()
        flash(_('You earned 10 tokens!'))
    return redirect(url_for('daily'))

@app.route('/daily/create_character', methods=['POST'])
@login_required
def create_character():
    name = request.form.get('name', 'Hero')
    char = PlayerCharacter(user_id=current_user.id, name=name)
    db.session.add(char)
    db.session.commit()
    return redirect(url_for('daily'))

@app.route('/daily/dungeon', methods=['POST'])
@login_required
def dungeon_action():
    import random
    action = request.form.get('action')
    char = db.session.scalar(sa.select(PlayerCharacter).where(PlayerCharacter.user_id == current_user.id))
    if not char:
        return redirect(url_for('daily'))
    result = []

    if char.is_dead:
        flash('Your character is dead. Buy a revival potion or wait until tomorrow.')
        return redirect(url_for('daily'))

    def recalc_stats():
        equipped = [i for i in char.inventory if i.is_equipped]
        base_str = char.strength
        base_spd = char.speed
        base_mag = char.magic
        base_rec = char.recovery
        base_sta = char.stamina
        base_luck = char.luck
        char.attack = 10 + base_str + sum(i.equipment.attack_bonus for i in equipped)
        char.defense = 5 + base_sta + sum(i.equipment.defense_bonus for i in equipped)
        char.max_hp = 100 + (char.level-1)*20 + sum(i.equipment.hp_bonus for i in equipped)

    if action == 'explore':
        if current_user.tokens < 5:
            flash('Not enough tokens!')
            return redirect(url_for('daily'))
        current_user.tokens -= 5
        roll = random.random()
        if roll < 0.5:
            scale = char.floor
            enemy = {
                'strength': random.randint(8,18) + scale*4,
                'speed': random.randint(8,18) + scale*4,
                'magic': random.randint(8,18) + scale*4,
                'stamina': random.randint(8,18) + scale*4,
                'hp': random.randint(30,80) + scale*15
            }
            result.append(f"⚔️ Enemy appeared! STR:{enemy['strength']} SPD:{enemy['speed']} MAG:{enemy['magic']} STA:{enemy['stamina']}")
            result.append(f"Your stats — STR:{char.strength} SPD:{char.speed} MAG:{char.magic} STA:{char.stamina}")
            from flask import session as flask_session
            flask_session['pending_enemy'] = enemy
            db.session.commit()
            return render_template('daily.html', already_signed=True, char=char,
                                   tokens=current_user.tokens, result=result, pending_fight=True, enemy=enemy)
        elif roll < 0.75:
            tokens_found = random.randint(1,10) + char.luck//2
            current_user.tokens += tokens_found
            result = [f"💰 Found {tokens_found} tokens!"]
        else:
            result = ["🌫️ Nothing here..."]

    elif action == 'fight':
        from flask import session as flask_session
        enemy = flask_session.pop('pending_enemy', None)
        if not enemy:
            return redirect(url_for('daily'))
        p_power = char.strength + char.speed + char.magic + char.stamina
        e_power = enemy['strength'] + enemy['speed'] + enemy['magic'] + enemy['stamina']
        diff = p_power - e_power
        if diff >= 0:
            hp_cost = max(5, enemy['hp'] - char.defense)
            new_hp = char.hp - hp_cost
            exp_gain = random.randint(20,50) + char.floor*5
            char.exp += exp_gain
            char.enemies_defeated += 1
            equipped_weapons = [i for i in char.inventory if i.is_equipped and i.equipment.equipment_type == 'weapon']
            for w in equipped_weapons:
                char.weapon_durability = max(0, char.weapon_durability - w.equipment.durability_drain)
            if char.weapon_durability == 0:
                result.append("💔 Your weapon broke!")
                for w in equipped_weapons:
                    w.is_equipped = False
                char.weapon_durability = 100
            result.append(f"✅ Victory! Lost {hp_cost} HP. Gained {exp_gain} EXP.")
            if new_hp < char.max_hp*0.4:
                char.is_dead = True
                char.death_date = datetime.now(timezone.utc)
                char.hp = 0
                result.append("💀 You survived the fight but succumbed to your wounds and died!")
                _apply_death_penalty(char)
            else:
                char.hp = new_hp
                if char.exp >= char.level*50:
                    char.level += 1; char.free_points += 5
                    char.hp = char.max_hp
                    result.append(f"🎉 Level Up! Now level {char.level}! +5 attribute points to allocate.")
        else:
            hp_loss = int(char.hp * 0.9)
            new_hp = char.hp - hp_loss
            if new_hp < 1 or new_hp < char.max_hp*0.4:
                char.is_dead = True
                char.death_date = datetime.now(timezone.utc)
                char.hp = 0
                result.append(f"💀 Defeated and died!")
                _apply_death_penalty(char)
            else:
                char.hp = new_hp
                result.append(f"💀 Defeated! Lost {hp_loss} HP.")
        recalc_stats()

    elif action == 'flee':
        from flask import session as flask_session
        flask_session.pop('pending_enemy', None)
        result = ["🏃 You fled safely."]

    elif action == 'rest':
        heal = min(char.recovery * 4, char.max_hp - char.hp)
        char.hp += heal
        result = [f"💤 Rested. Recovered {heal} HP."]

    elif action == 'descend':
        if char.hp < char.max_hp * 0.3:
            result = ["❌ Too injured to descend. Rest first."]
        elif char.floor < 20:
            char.floor += 1; char.total_floors_cleared += 1
            result = [f"⬇️ Floor {char.floor}! Recommended level: {(char.floor-1)*10+1}-{char.floor*10}"]
        else:
            result = ["🏆 Maximum depth reached!"]

    elif action == 'ascend':
        if char.floor > 1:
            char.floor -= 1
            result = [f"⬆️ Floor {char.floor}! Recommended level: {(char.floor-1)*10+1}-{char.floor*10}"]
        else:
            result = ["🏠 Already at the topmost floor!"]

    db.session.commit()
    if char.is_dead:
        return redirect(url_for('daily'))
    return render_template('daily.html', already_signed=True, char=char,
                           tokens=current_user.tokens, result=result, pending_fight=False)


def _apply_death_penalty(char):
    import random
    char.level = max(1, char.level - 1)
    items = [i for i in char.inventory]
    if items:
        lose_count = max(1, int(len(items)*0.2))
        for item in random.sample(items, min(lose_count, len(items))):
            db.session.delete(item)


@app.route('/daily/allocate', methods=['POST'])
@login_required
def allocate_point():
    char = db.session.scalar(sa.select(PlayerCharacter).where(PlayerCharacter.user_id == current_user.id))
    stat = request.form.get('stat')
    if char and char.free_points > 0 and stat in ('strength','speed','magic','recovery','stamina','luck'):
        setattr(char, stat, getattr(char, stat) + 1)
        char.free_points -= 1
        db.session.commit()
    return redirect(url_for('daily'))


@app.route('/daily/revive', methods=['POST'])
@login_required
def revive_character():
    char = db.session.scalar(sa.select(PlayerCharacter).where(PlayerCharacter.user_id == current_user.id))
    if char and char.is_dead:
        if current_user.tokens < 50:
            flash('Need 50 tokens to revive!')
        else:
            current_user.tokens -= 50
            char.is_dead = False
            char.hp = char.max_hp
            db.session.commit()
            flash('Revived!')
    return redirect(url_for('daily'))

@app.route('/admin/tokens', methods=['GET', 'POST'])
@login_required
def admin_tokens():
    if not current_user.is_admin:
        abort(403)
    users = db.session.scalars(sa.select(User)).all()
    if request.method == 'POST':
        user_id = request.form.get('user_id', type=int)
        amount = request.form.get('amount', type=int)
        user = db.session.get(User, user_id)
        if user and amount:
            user.tokens += amount
            db.session.commit()
            flash(f'Added {amount} tokens to {user.username}')
    return render_template('admin_tokens.html', users=users)


@app.route('/daily/shop')
@login_required
def equipment_shop():
    char = db.session.scalar(sa.select(PlayerCharacter).where(PlayerCharacter.user_id == current_user.id))
    if not char:
        return redirect(url_for('daily'))
    items = db.session.scalars(sa.select(Equipment)).all()
    owned_ids = {i.equipment_id for i in db.session.scalars(sa.select(PlayerInventory).where(PlayerInventory.player_id == char.id)).all()}
    return render_template('shop.html', items=items, char=char, tokens=current_user.tokens, owned_ids=owned_ids)

@app.route('/daily/buy/<int:item_id>', methods=['POST'])
@login_required
def buy_equipment(item_id):
    char = db.session.scalar(sa.select(PlayerCharacter).where(PlayerCharacter.user_id == current_user.id))
    item = db.session.get(Equipment, item_id)
    if not char or not item:
        abort(404)
    already_owned = db.session.scalar(sa.select(PlayerInventory).where(PlayerInventory.player_id == char.id, PlayerInventory.equipment_id == item_id))
    if already_owned:
        flash('Already owned!')
    elif char.level < item.required_level:
        flash(f'Requires level {item.required_level}!')
    elif current_user.tokens < item.token_cost:
        flash('Not enough tokens!')
    else:
        current_user.tokens -= item.token_cost
        db.session.add(PlayerInventory(player_id=char.id, equipment_id=item_id))
        db.session.commit()
        flash(f'Bought {item.emoji} {item.name}!')
    return redirect(url_for('equipment_shop'))

@app.route('/daily/equip/<int:inv_id>', methods=['POST'])
@login_required
def equip_item(inv_id):
    char = db.session.scalar(sa.select(PlayerCharacter).where(PlayerCharacter.user_id == current_user.id))
    inv = db.session.get(PlayerInventory, inv_id)
    if not char or not inv or inv.player_id != char.id:
        abort(404)
    # unequip same type
    same_type = db.session.scalars(
        sa.select(PlayerInventory).where(
            PlayerInventory.player_id == char.id,
            PlayerInventory.is_equipped == True
        ).join(Equipment).where(Equipment.equipment_type == inv.equipment.equipment_type)
    ).all()
    for old in same_type:
        old.is_equipped = False
    inv.is_equipped = not inv.is_equipped
    # recalculate stats
    equipped = db.session.scalars(
        sa.select(PlayerInventory).where(PlayerInventory.player_id == char.id, PlayerInventory.is_equipped == True)
    ).all()
    char.attack = 10 + sum(i.equipment.attack_bonus for i in equipped)
    char.defense = 5 + sum(i.equipment.defense_bonus for i in equipped)
    char.max_hp = 100 + (char.level - 1) * 20 + sum(i.equipment.hp_bonus for i in equipped)
    db.session.commit()
    return redirect(url_for('daily'))

@app.route('/ranking')
@login_required
def ranking():
    chars = db.session.scalars(
        sa.select(PlayerCharacter).order_by(
            PlayerCharacter.level.desc(),
            PlayerCharacter.exp.desc()
        )
    ).all()
    return render_template('ranking.html', chars=chars)

@app.route('/all_users')
@login_required
def all_users():
    if not current_user.is_admin:
        abort(403)
    users = User.query.order_by(User.username).all()
    return render_template('all_users.html', users=users)

@app.route('/daily/cast_spell', methods=['POST'])
@login_required
def cast_spell():
    # Add your spell logic here later
    flash('Spell casting not implemented yet.')
    return redirect(url_for('daily'))

@app.route('/daily/buy_magic_potion', methods=['POST'])
@login_required
def buy_magic_potion():
    flash('Magic potion feature coming soon!')
    return redirect(url_for('equipment_shop'))

@app.route('/daily/buy_spellbook', methods=['POST'])
@login_required
def buy_spellbook():
    flash('Spellbook feature coming soon!')
    return redirect(url_for('equipment_shop'))

@app.route('/team')
def team_9094():
    return render_template('team.html', title=_('Team 9094'))

@app.route('/team/mechanical')
def team_mechanical():
    return render_template('team_sub.html', title=_('Mechanical'), icon='⚙️', sub_name='MECHANICAL',
        overview="The mechanical subteam designs, fabricates, and assembles the physical robot — drivetrain, manipulators, and structural framing — each build season, translating strategy and CAD models into a working machine on a strict six-week timeline.",
        tags=['DRIVETRAIN', 'MANIPULATORS', 'FABRICATION', 'MACHINING', 'ASSEMBLY', 'PROTOTYPING'],
        notes="Specific mechanisms and season highlights can be added here as the team documents them.")

@app.route('/team/electrical')
def team_electrical():
    return render_template('team_sub.html', title=_('Electrical'), icon='🔌', sub_name='ELECTRICAL',
        overview="The electrical subteam wires and maintains the robot's power distribution, motor controllers, sensors, and onboard electronics — ensuring every mechanical and software system has clean, reliable power and signal throughout competition.",
        tags=['WIRING', 'POWER DISTRIBUTION', 'SENSORS', 'MOTOR CONTROLLERS', 'PNEUMATICS', 'ROBORIO'],
        notes="Specific electrical architecture details can be added here as the team documents them.")

@app.route('/team/software')
def team_software():
    return render_template('team_sub.html', title=_('Software'), icon='💻', sub_name='SOFTWARE',
        overview="The software subteam programs the robot's autonomous routines and driver-controlled operation, working in Java or C++ on the WPILib framework, along with vision processing and telemetry systems used during matches.",
        tags=['JAVA', 'AUTONOMOUS', 'TELEOP', 'WPILIB', 'VISION PROCESSING', 'PID TUNING'],
        notes="Specific codebase and season architecture details can be added here as the team documents them.")

@app.route('/team/strategy')
def team_strategy():
    return render_template('team_sub.html', title=_('Strategy'), icon='🎯', sub_name='STRATEGY',
        overview="The strategy subteam analyzes each season's game manual, scouts opposing teams during competition, and develops match-by-match alliance strategy — bridging engineering decisions with real competitive outcomes on the field.",
        tags=['GAME ANALYSIS', 'SCOUTING', 'ALLIANCE SELECTION', 'MATCH STRATEGY', 'DATA ANALYSIS'],
        notes="Specific scouting systems and competition results can be added here as the team documents them.")

@app.route('/team/outreach')
def team_outreach():
    return render_template('team_sub.html', title=_('Outreach'), icon='🤝', sub_name='OUTREACH',
        overview="The outreach subteam extends the EarthQuakers' mission beyond the build season — engaging the Friends Central School community and beyond to grow interest in STEM and FIRST Robotics through events, mentorship, and demonstrations.",
        tags=['STEM ADVOCACY', 'COMMUNITY EVENTS', 'MENTORSHIP', 'FUNDRAISING', 'SOCIAL MEDIA'],
        notes="Specific events and partnerships can be added here as the team documents them.")

@app.route('/team/cad')
def team_cad():
    return render_template('team_sub.html', title=_('CAD'), icon='📐', sub_name='CAD',
        overview="The CAD subteam models every robot component in 3D before it's built, iterating on design in simulation to validate fit, clearance, and mechanism function ahead of fabrication — the digital blueprint behind every EarthQuakers robot.",
        tags=['3D MODELING', 'DESIGN ITERATION', 'SIMULATION', 'ONSHAPE', 'ASSEMBLY MODELING'],
        notes="Specific CAD software and design workflow details can be added here as the team documents them.")

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