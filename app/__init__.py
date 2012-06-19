
from functools import wraps

from flask import current_app, redirect, render_template, request, flash, \
     url_for, session, Response
from flask.ext import security
from flask.ext.security import Security, LoginForm, UserMixin, RoleMixin, \
     current_user, login_required, login_user
from flask.ext.security.datastore import SQLAlchemyUserDatastore
from flask.ext.social import Social, social_login_failed, get_display_name
from flask.ext.social.datastore import SQLAlchemyConnectionDatastore
from flask.ext.sqlalchemy import SQLAlchemy

from .assets import init_assets
from .forms import RegisterForm
from .helpers import Flask
from .middleware import MethodRewriteMiddleware


class SocialLoginError(Exception):
    def __init__(self, provider_id):
        self.provider_id = provider_id


def check_auth(username, password):
    creds = current_app.config['ADMIN_CREDENTIALS'].split(',')
    return username == creds[0] and password == creds[1]


def authenticate():
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


def create_app():
    app = Flask(__name__)
    app.config.from_yaml(app.root_path)
    app.config.from_heroku()
    app.wsgi_app = MethodRewriteMiddleware(app.wsgi_app)

    init_assets(app)

    db = SQLAlchemy(app)

    roles_users = db.Table('roles_users',
        db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
        db.Column('role_id', db.Integer(), db.ForeignKey('role.id')))

    class Role(db.Model, RoleMixin):
        id = db.Column(db.Integer(), primary_key=True)
        name = db.Column(db.String(80), unique=True)
        description = db.Column(db.String(255))

    class User(db.Model, UserMixin):
        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(255), unique=True)
        password = db.Column(db.String(120))
        active = db.Column(db.Boolean())
        roles = db.relationship('Role', secondary=roles_users,
                                backref=db.backref('users', lazy='dynamic'))

    class Connection(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
        provider_id = db.Column(db.String(255))
        provider_user_id = db.Column(db.String(255))
        access_token = db.Column(db.String(255))
        secret = db.Column(db.String(255))
        display_name = db.Column(db.String(255))
        profile_url = db.Column(db.String(512))
        image_url = db.Column(db.String(512))
        rank = db.Column(db.Integer)

    Security(app, SQLAlchemyUserDatastore(db, User, Role),
             registerable=False, recoverable=False)

    Social(app, SQLAlchemyConnectionDatastore(db, Connection))

    try:
        db.create_all()
    except Exception, e:
        app.logger.error(str(e))

    @app.context_processor
    def template_extras():
        return dict(
            google_analytics_id=app.config.get('GOOGLE_ANALYTICS_ID', None)
        )

    @social_login_failed.connect_via(app)
    def on_social_login_failed(sender, provider_id, oauth_response):
        app.logger.debug('Social Login Failed: provider_id=%s'
                         '&oauth_response=%s' % (provider_id, oauth_response))

        # Save the oauth response in the session so we can make the connection
        # later after the user possibly registers
        session['last_oauth_response'] = dict(provider_id=provider_id,
                                              oauth_response=oauth_response)
        raise SocialLoginError(provider_id)

    @app.errorhandler(SocialLoginError)
    def social_login_error(error):
        return redirect(url_for('register',
                                provider_id=error.provider_id,
                                social_login_failed=1))

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/login')
    def login():
        if current_user.is_authenticated():
            return redirect(request.referrer or '/')

        return render_template('login.html', form=LoginForm())

    @app.route('/register', methods=['GET', 'POST'])
    @app.route('/register/<provider_id>')
    def register(provider_id=None):
        app.logger.debug("/register [%s]" % request.method)

        if current_user.is_authenticated():
            return redirect(request.referrer or '/')

        form = RegisterForm()

        if form.validate_on_submit():
            user = current_app.security.datastore.create_user(
                    email=form.email.data,
                    password=form.password.data)

            # See if there was an attempted social login prior to registering
            # and if there was use the provider connect_handler to save a connection
            social_login_response = session.pop('last_oauth_response', None)

            if social_login_response:
                provider_id = social_login_response['provider_id']
                oauth_response = social_login_response['oauth_response']

                provider = getattr(app.social, provider_id)
                provider.connect_handler(oauth_response, user_id=str(user.id))

            if login_user(user, remember=True):
                flash('Account created successfully', 'info')
                return redirect(url_for('profile'))

            return render_template('thanks.html', user=user)

        social_login_failed = int(request.args.get('social_login_failed', 0))
        provider_name = None

        if social_login_failed and provider_id:
            provider_name = get_display_name(provider_id)

        return render_template('register.html', form=form,
                               social_login_failed=social_login_failed,
                               provider_name=provider_name)

    @app.route('/profile')
    @login_required
    def profile():
        return render_template('profile.html',
            twitter_conn=current_app.social.twitter.get_connection(),
            facebook_conn=current_app.social.facebook.get_connection())

    @app.route('/profile/<provider_id>/post', methods=['POST'])
    @login_required
    def social_post(provider_id):
        message = request.form.get('message', None)

        if message:
            conn = getattr(current_app.social, provider_id).get_connection()
            api = conn['api']

            if provider_id == 'twitter':
                display_name = 'Twitter'
                api.PostUpdate(message)
            if provider_id == 'facebook':
                display_name = 'Facebook'
                api.put_object("me", "feed", message=message)

            flash('Message posted to %s: %s' % (display_name, message), 'info')

        return redirect(url_for('profile'))

    @app.route('/admin')
    @requires_auth
    def admin():
        users = security.User.query.all()
        user_count = len(users)
        return render_template('admin.html',
                                users=users,
                                user_count=user_count)

    return app
