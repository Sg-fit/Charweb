from datetime import datetime, timezone
from time import time
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from flask import current_app
from sqlalchemy import func
from app import app, db, login
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from hashlib import md5
import jwt
from app.search import add_to_index, remove_from_index, query_index


class User(UserMixin, db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    username: so.Mapped[str] = so.mapped_column(sa.String(64), index=True, unique=True)
    email: so.Mapped[str] = so.mapped_column(sa.String(120), index=True, unique=True)
    password_hash: so.Mapped[Optional[str]] = so.mapped_column(sa.String(256))
    about_me: so.Mapped[Optional[str]] = so.mapped_column(sa.String(140))
    last_seen: so.Mapped[Optional[datetime]] = so.mapped_column(
        default=lambda: datetime.now(timezone.utc))
    last_message_at: so.Mapped[Optional[datetime]] = so.mapped_column(default=None, nullable=True)
    tokens: so.Mapped[int] = so.mapped_column(default=0)
    terms_accepted_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        default=None, nullable=True)
    is_admin: so.Mapped[bool] = so.mapped_column(default=False)
    last_user_agent: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    last_accept_language: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120), nullable=True)

    posts: so.WriteOnlyMapped['Post'] = so.relationship(back_populates='author')

    def __repr__(self):
        return '<User {}>'.format(self.username)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def avatar(self, size):
        digest = md5(self.email.lower().encode('utf-8')).hexdigest()
        return f'https://www.gravatar.com/avatar/{digest}?d=identicon&s={size}'
    
    def get_reset_password_token(self, expires_in=600):
        return jwt.encode(
            {'reset_password': self.id, 'exp': time() + expires_in},
            app.config['SECRET_KEY'], algorithm='HS256')

    @staticmethod
    def verify_reset_password_token(token):
        try:
            id = jwt.decode(token, app.config['SECRET_KEY'],
                            algorithms=['HS256'])['reset_password']
        except Exception:
            return
        return db.session.get(User, id)

    def get_login_token(self, expires_in=2592000):
        """Signed, GET-accessible login link for agents/harnesses that can
        only navigate (no form POST). Defaults to a 30-day expiry since
        it's meant for repeated automated use, not a one-time email link."""
        return jwt.encode(
            {'quick_login': self.id, 'exp': time() + expires_in},
            app.config['SECRET_KEY'], algorithm='HS256')

    @staticmethod
    def verify_login_token(token):
        try:
            id = jwt.decode(token, app.config['SECRET_KEY'],
                            algorithms=['HS256'])['quick_login']
        except Exception:
            return
        return db.session.get(User, id)


@login.user_loader
def load_user(id):
    return db.session.get(User, int(id))


class SearchableMixin(object):
    @classmethod
    def _db_search(cls, expression, page, per_page):
        query = sa.select(cls).where(cls.body.ilike(f'%{expression}%'))
        total = db.session.scalar(sa.select(func.count()).select_from(query.subquery()))
        results = db.session.scalars(
            query.order_by(cls.timestamp.desc())
                 .offset((page - 1) * per_page)
                 .limit(per_page))
        return results, total

    @classmethod
    def search(cls, expression, page, per_page):
        ids, total = query_index(cls.__tablename__, expression, page, per_page)
        if ids is None:
            return cls._db_search(expression, page, per_page)
        if total == 0:
            return [], 0
        when = []
        for i in range(len(ids)):
            when.append((ids[i], i))
        query = sa.select(cls).where(cls.id.in_(ids)).order_by(
            db.case(*when, value=cls.id))
        return db.session.scalars(query), total

    @classmethod
    def before_commit(cls, session):
        session._changes = {
            'add': list(session.new),
            'update': list(session.dirty),
            'delete': list(session.deleted)
        }

    @classmethod
    def after_commit(cls, session):
        for obj in session._changes['add']:
            if isinstance(obj, SearchableMixin):
                add_to_index(obj.__tablename__, obj)
        for obj in session._changes['update']:
            if isinstance(obj, SearchableMixin):
                add_to_index(obj.__tablename__, obj)
        for obj in session._changes['delete']:
            if isinstance(obj, SearchableMixin):
                remove_from_index(obj.__tablename__, obj)
        session._changes = None


class Post(SearchableMixin, db.Model):
    __searchable__ = ['body']   
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    body: so.Mapped[str] = so.mapped_column(sa.String(500))
    timestamp: so.Mapped[datetime] = so.mapped_column(
        index=True, default=lambda: datetime.now(timezone.utc))
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(User.id), index=True)
    image_filename: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    language: so.Mapped[str] = so.mapped_column(sa.String(10), default='en')

    author: so.Mapped[User] = so.relationship(back_populates='posts')
    
    comments: so.Mapped[list['Comment']] = so.relationship(
        'Comment', back_populates='post', cascade='all, delete-orphan')
    
    likes: so.Mapped[list['Like']] = so.relationship(
        'Like', back_populates='post', cascade='all, delete-orphan')

    def __repr__(self):
        return 'Post {}'.format(self.body)

    @classmethod
    def reindex(cls):
        for obj in db.session.scalars(sa.select(cls)):
            add_to_index(cls.__tablename__, obj)


class Comment(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    body: so.Mapped[str] = so.mapped_column(sa.String(500))
    timestamp: so.Mapped[datetime] = so.mapped_column(
        index=True, default=lambda: datetime.now(timezone.utc))
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(User.id))
    post_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('post.id'))

    author: so.Mapped[User] = so.relationship('User')
    post: so.Mapped['Post'] = so.relationship('Post', back_populates='comments')

    def __repr__(self):
        return f'<Comment {self.body[:50]}...>'


class Like(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), index=True)
    post_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('post.id'), index=True)
    timestamp: so.Mapped[datetime] = so.mapped_column(
        default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        sa.UniqueConstraint('user_id', 'post_id', name='uq_user_post_like'),
    )

    user: so.Mapped['User'] = so.relationship('User')
    post: so.Mapped['Post'] = so.relationship('Post', back_populates='likes')

    def __repr__(self):
        return f'<Like user={self.user_id} post={self.post_id}>'


class TrackedAction(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    user_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey(User.id), index=True, nullable=True)
    session_uid: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), index=True, nullable=True)
    action_type: so.Mapped[str] = so.mapped_column(sa.String(20))
    target: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120))
    timestamp: so.Mapped[datetime] = so.mapped_column(
        index=True, default=lambda: datetime.now(timezone.utc))
    details: so.Mapped[Optional[str]] = so.mapped_column(sa.Text)

    user: so.Mapped[Optional[User]] = so.relationship()

    def __repr__(self):
        return f'<TrackedAction {self.action_type} user_id={self.user_id}>'


class UserSession(db.Model):
    """One row per browser session: user-agent identification plus the AI-defense
    system's rule-based flag and RandomForest behavioral score for that session."""
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    session_uid: so.Mapped[str] = so.mapped_column(sa.String(64), unique=True, index=True)
    user_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey(User.id), index=True, nullable=True)
    user_agent: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    accept_language: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120), nullable=True)
    ua_bot_flag: so.Mapped[bool] = so.mapped_column(default=False)
    ua_bot_reason: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120), nullable=True)
    ai_probability: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    ai_prediction: so.Mapped[Optional[str]] = so.mapped_column(sa.String(10), nullable=True)
    first_seen: so.Mapped[datetime] = so.mapped_column(default=lambda: datetime.now(timezone.utc))
    last_seen: so.Mapped[datetime] = so.mapped_column(default=lambda: datetime.now(timezone.utc))
    last_scored_at: so.Mapped[Optional[datetime]] = so.mapped_column(nullable=True)

    user: so.Mapped[Optional[User]] = so.relationship()

    def __repr__(self):
        return f'<UserSession {self.session_uid} user_id={self.user_id}>'

class Message(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    sender_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'))
    recipient_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'))
    body: so.Mapped[str] = so.mapped_column(sa.Text)
    timestamp: so.Mapped[datetime] = so.mapped_column(
        default=lambda: datetime.now(timezone.utc))

    sender: so.Mapped[User] = so.relationship('User', foreign_keys=[sender_id])
    recipient: so.Mapped[User] = so.relationship('User', foreign_keys=[recipient_id])

    def __repr__(self):
        return f'<Message {self.sender_id}->{self.recipient_id}>'

db.event.listen(db.session, 'before_commit', SearchableMixin.before_commit)
db.event.listen(db.session, 'after_commit', SearchableMixin.after_commit)

class Notification(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), index=True)
    message: so.Mapped[str] = so.mapped_column(sa.String(255))
    is_read: so.Mapped[bool] = so.mapped_column(default=False)
    timestamp: so.Mapped[datetime] = so.mapped_column(
        default=lambda: datetime.now(timezone.utc))
    user: so.Mapped[User] = so.relationship('User', foreign_keys=[user_id])

class DailySignIn(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), index=True)
    date: so.Mapped[datetime] = so.mapped_column(default=lambda: datetime.now(timezone.utc))
    tokens_earned: so.Mapped[int] = so.mapped_column(default=10)
    user: so.Mapped[User] = so.relationship('User', foreign_keys=[user_id])

class PlayerCharacter(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), unique=True, index=True)
    name: so.Mapped[str] = so.mapped_column(sa.String(64), default='Hero')
    hp: so.Mapped[int] = so.mapped_column(default=100)
    max_hp: so.Mapped[int] = so.mapped_column(default=100)
    attack: so.Mapped[int] = so.mapped_column(default=10)
    defense: so.Mapped[int] = so.mapped_column(default=5)
    level: so.Mapped[int] = so.mapped_column(default=1)
    exp: so.Mapped[int] = so.mapped_column(default=0)
    tokens: so.Mapped[int] = so.mapped_column(default=0)
    floor: so.Mapped[int] = so.mapped_column(default=1)
    user: so.Mapped[User] = so.relationship('User', foreign_keys=[user_id])
    total_floors_cleared: so.Mapped[int] = so.mapped_column(default=0)
    enemies_defeated: so.Mapped[int] = so.mapped_column(default=0)
    inventory: so.Mapped[list['PlayerInventory']] = so.relationship('PlayerInventory', foreign_keys='PlayerInventory.player_id')
    strength: so.Mapped[int] = so.mapped_column(default=10)
    speed: so.Mapped[int] = so.mapped_column(default=10)
    magic: so.Mapped[int] = so.mapped_column(default=10)
    recovery: so.Mapped[int] = so.mapped_column(default=5)
    stamina: so.Mapped[int] = so.mapped_column(default=10)
    luck: so.Mapped[int] = so.mapped_column(default=5)
    weapon_durability: so.Mapped[int] = so.mapped_column(default=100)
    free_points: so.Mapped[int] = so.mapped_column(default=0)
    is_dead: so.Mapped[bool] = so.mapped_column(default=False)
    death_date: so.Mapped[Optional[datetime]] = so.mapped_column(default=None, nullable=True)

class Equipment(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    name: so.Mapped[str] = so.mapped_column(sa.String(64))
    equipment_type: so.Mapped[str] = so.mapped_column(sa.String(20))  # weapon/helmet/armor/boots/accessory
    rarity: so.Mapped[str] = so.mapped_column(sa.String(20), default='common')  # common/rare/epic/legendary
    attack_bonus: so.Mapped[int] = so.mapped_column(default=0)
    defense_bonus: so.Mapped[int] = so.mapped_column(default=0)
    hp_bonus: so.Mapped[int] = so.mapped_column(default=0)
    emoji: so.Mapped[str] = so.mapped_column(sa.String(10), default='⚔️')
    token_cost: so.Mapped[int] = so.mapped_column(default=20)
    strength_bonus: so.Mapped[int] = so.mapped_column(default=0)
    speed_bonus: so.Mapped[int] = so.mapped_column(default=0)
    magic_bonus: so.Mapped[int] = so.mapped_column(default=0)
    recovery_bonus: so.Mapped[int] = so.mapped_column(default=0)
    stamina_bonus: so.Mapped[int] = so.mapped_column(default=0)
    luck_bonus: so.Mapped[int] = so.mapped_column(default=0)
    durability_drain: so.Mapped[int] = so.mapped_column(default=10)
    required_level: so.Mapped[int] = so.mapped_column(default=1)

class PlayerInventory(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    player_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('player_character.id'), index=True)
    equipment_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('equipment.id'))
    is_equipped: so.Mapped[bool] = so.mapped_column(default=False)
    acquired_at: so.Mapped[datetime] = so.mapped_column(default=lambda: datetime.now(timezone.utc))

    player: so.Mapped[PlayerCharacter] = so.relationship('PlayerCharacter', foreign_keys=[player_id])
    equipment: so.Mapped[Equipment] = so.relationship('Equipment', foreign_keys=[equipment_id])