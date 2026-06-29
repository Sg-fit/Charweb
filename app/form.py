from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, ValidationError, Email, EqualTo, Length
from app.models import User
from app import db
import sqlalchemy as sa
from flask_babel import lazy_gettext as _

class LoginForm(FlaskForm):
    username = StringField(_('Username'), validators=[DataRequired(message=_('This field is required.'))])
    password = PasswordField(_('Password'), validators=[DataRequired(message=_('This field is required.'))])
    remember_me = BooleanField(_('Remember Me'))
    submit = SubmitField(_('Sign In'))

class RegistrationForm(FlaskForm):
    username = StringField(_('Username'), validators=[DataRequired(message=_('This field is required.'))])
    email = StringField(_('Email'), validators=[DataRequired(message=_('This field is required.')), Email(message=_('Invalid email address.'))])
    password = PasswordField(_('Password'), validators=[DataRequired(message=_('This field is required.'))])
    password2 = PasswordField(
        _('Repeat Password'), validators=[DataRequired(message=_('This field is required.')), EqualTo('password', message=_('Passwords must match'))])
    accept_terms = BooleanField(
        _('I have read and agree to the Terms of Service and Privacy Policy'),
        validators=[DataRequired(message=_('You must accept the Terms of Service and Privacy Policy to create an account.'))])
    submit = SubmitField(_('Register'))

    def validate_username(self, username):
        user = db.session.scalar(
            sa.select(User).where(User.username == username.data))
        if user is not None:
            raise ValidationError(_('Please use a different username.'))

    def validate_email(self, email):
        user = db.session.scalar(
            sa.select(User).where(User.email == email.data))
        if user is not None:
            raise ValidationError(_('Please use a different email address.'))
        
class EditProfileForm(FlaskForm):
    username = StringField(_('Username'), validators=[DataRequired(message=_('This field is required.'))])
    about_me = TextAreaField(_('About me'), validators=[Length(min=0, max=140, message=_('Field must be between %(min)d and %(max)d characters long.'))])
    submit = SubmitField(_('Submit'))

    def __init__(self, original_username, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_username = original_username

    def validate_username(self, username):
        if username.data != self.original_username:
            user = db.session.scalar(sa.select(User).where(
                User.username == username.data))
            if user is not None:
                raise ValidationError(_('Please use a different username.'))
            
class PostForm(FlaskForm):
    post = TextAreaField(_('Say something'), validators=[
        DataRequired(message=_('This field is required.')), Length(min=1, max=140, message=_('Field must be between %(min)d and %(max)d characters long.'))])
    submit = SubmitField(_('Submit'))

class EmptyForm(FlaskForm):
    submit = SubmitField(_('Submit'))

class ResetPasswordRequestForm(FlaskForm):
    email = StringField(_('Email'), validators=[DataRequired(message=_('This field is required.')), Email(message=_('Invalid email address.'))])
    submit = SubmitField(_('Request Password Reset'))

class ResetPasswordForm(FlaskForm):
    password = PasswordField(_('Password'), validators=[DataRequired(message=_('This field is required.'))])
    password2 = PasswordField(
        _('Repeat Password'), validators=[DataRequired(message=_('This field is required.')), EqualTo('password', message=_('Passwords must match'))])
    submit = SubmitField(_('Request Password Reset'))
