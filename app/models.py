#model is basically a class that defines the structure of the data that will be stored in the database. It is used to create tables and manage the data in those tables. In this code, we have two models: User and Post. The User model represents a user of the application, while the Post model represents a post made by a user. Each model has its own set of fields and methods that define how the data is stored and accessed.
#It is the model for what a user account should be like and its accoring data
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
    username: so.Mapped[str] = so.mapped_column(sa.String(64), index=True,
                                                unique=True)
    email: so.Mapped[str] = so.mapped_column(sa.String(120), index=True,
                                             unique=True)
    password_hash: so.Mapped[Optional[str]] = so.mapped_column(sa.String(256))
    about_me: so.Mapped[Optional[str]] = so.mapped_column(sa.String(140))
    last_seen: so.Mapped[Optional[datetime]] = so.mapped_column(
        default=lambda: datetime.now(timezone.utc))
    terms_accepted_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        default=None, nullable=True)
    is_admin: so.Mapped[bool] = so.mapped_column(default=False)
    posts: so.WriteOnlyMapped['Post'] = so.relationship(
        back_populates='author')
    # following: so.WriteOnlyMapped['User'] = so.relationship(
    #     secondary=followers, primaryjoin=(followers.c.follower_id == id),
    #     secondaryjoin=(followers.c.followed_id == id),
    #     back_populates='followers')
    # followers: so.WriteOnlyMapped['User'] = so.relationship(
    #     secondary=followers, primaryjoin=(followers.c.followed_id == id),
    #     secondaryjoin=(followers.c.follower_id == id),
    #     back_populates='following')

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
    body: so.Mapped[str] = so.mapped_column(sa.String(140))
    timestamp: so.Mapped[datetime] = so.mapped_column(
        index=True, default=lambda: datetime.now(timezone.utc))
    # a default argument, and passed a lambda function that returns the current time in the UTC timezone.
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(User.id),
        index=True)#The user_id field was initialized as a foreign key to User.id, which means that it references values from the id column in the users table. Since not all databases automatically create an index for foreign keys, the index=True option is added explicitly, so that searches based on this column are optimized.

    author: so.Mapped[User] = so.relationship(back_populates='posts')
    #The first argument to so.relationship() is the model class that represents the other side of the relationship. This argument can be provided as a string, which is necessary when the class is defined later in the module. The back_populates arguments reference the name of the relationship attribute on the other side, so that SQLAlchemy knows that these attributes refer to the two sides of the same relationship.

    def __repr__(self):
        return 'Post {}'.format(self.body)
    
    # def __tablename__(self):
    #     return 'posts__data'
    language: so.Mapped[str] = so.mapped_column(sa.String(10), default='en')

    @classmethod
    def reindex(cls):
        for obj in db.session.scalars(sa.select(cls)):
            add_to_index(cls.__tablename__, obj)

class TrackedAction(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    user_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey(User.id), index=True, nullable=True)
    action_type: so.Mapped[str] = so.mapped_column(sa.String(20))
    target: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120))
    timestamp: so.Mapped[datetime] = so.mapped_column(
        index=True, default=lambda: datetime.now(timezone.utc))
    details: so.Mapped[Optional[str]] = so.mapped_column(sa.Text)

    user: so.Mapped[Optional[User]] = so.relationship()

    def __repr__(self):
        return f'<TrackedAction {self.action_type} user_id={self.user_id}>'
    
db.event.listen(db.session, 'before_commit', SearchableMixin.before_commit)
db.event.listen(db.session, 'after_commit', SearchableMixin.after_commit)