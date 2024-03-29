import datetime
import os
import queue
import traceback
from functools import wraps

import markdown
import requests
from flask import Flask, Response, abort, flash, redirect, render_template, request, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_sse import sse
from requests_oauthlib import OAuth2Session

app = Flask(__name__, static_url_path='/static')

app.debug = os.environ.get('DEBUG', '').lower() == 'true'

with open('secret', 'rb') as f:
    app.config['SECRET_KEY'] = f.read()

if app.debug:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite'
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://admin:cybercyber@database/project'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

app.config["REDIS_URL"] = "redis://redis"
app.register_blueprint(sse, url_prefix='/notifications')

db = SQLAlchemy(app)

MARKDOWN_EXTENSIONS = ['pymdownx.magiclink', 'pymdownx.tabbed', 'pymdownx.arithmatex', 'pymdownx.details',
                       'pymdownx.emoji', 'pymdownx.highlight', 'pymdownx.inlinehilite', 'pymdownx.keys',
                       'pymdownx.progressbar', 'pymdownx.smartsymbols', 'pymdownx.snippets', 'pymdownx.tabbed',
                       'pymdownx.tasklist', 'pymdownx.tilde']


### DB ###

class SSHKey(db.Model):
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    uid = db.Column(db.BigInteger, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    key = db.Column(db.String(1000), nullable=False)

    def __init__(self, uid, name, key):
        self.uid = uid
        self.name = name
        self.key = key


class Wireguard(db.Model):
    filename = db.Column(db.String(100), primary_key=True)
    uid = db.Column(db.BigInteger, nullable=False)

    def __init__(self, filename, uid):
        self.filename = filename
        self.uid = uid


class HomeMessage(db.Model):
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    message = db.Column(db.Text, nullable=False)
    username = db.Column(db.Text, nullable=False)

    def __init__(self, message, username):
        self.message = message
        self.username = username


class Notification(db.Model):
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    notification = db.Column(db.Text, nullable=False)
    time = db.Column(db.DateTime, nullable=False)

    def __init__(self, notification):
        self.notification = notification
        self.time = datetime.datetime.now()


### Discord OAUTH ###

OAUTH2_CLIENT_ID = os.environ['OAUTH2_CLIENT_ID']
OAUTH2_CLIENT_SECRET = os.environ['OAUTH2_CLIENT_SECRET']
OAUTH2_REDIRECT_URI = os.environ['OAUTH2_REDIRECT_URI']

API_BASE_URL = os.environ.get('API_BASE_URL', 'https://discordapp.com/api')
AUTHORIZATION_BASE_URL = API_BASE_URL + '/oauth2/authorize'
TOKEN_URL = API_BASE_URL + '/oauth2/token'

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = 'true'


def token_updater(token):
    session['oauth2_token'] = token


def make_session(token=None, state=None, scope=None):
    return OAuth2Session(
        client_id=OAUTH2_CLIENT_ID,
        token=token,
        state=state,
        scope=scope,
        redirect_uri=OAUTH2_REDIRECT_URI,
        auto_refresh_kwargs={
            'client_id': OAUTH2_CLIENT_ID,
            'client_secret': OAUTH2_CLIENT_SECRET,
        },
        auto_refresh_url=TOKEN_URL,
        token_updater=token_updater)


#####################

def is_logged_in(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'uid' in session:
            return f(*args, **kwargs)
        else:
            flash('You have to log in to perform this action', 'danger')
            return redirect('/')

    return wrap


@app.route('/')
def index():
    if 'username' in session:
        return redirect('/home')
    return render_template('index.html')


@app.route('/login')
def login():
    if 'oauth2_token' not in session:
        scope = request.args.get(
            'scope',
            'identify guilds guilds.members.read')
        discord = make_session(scope=scope.split(' '))
        authorization_url, state = discord.authorization_url(AUTHORIZATION_BASE_URL)
        session['oauth2_state'] = state
        return redirect(authorization_url)

    else:
        discord = make_session(token=session.get('oauth2_token'))
        me = discord.get(API_BASE_URL + '/users/@me').json()
        guilds = discord.get(API_BASE_URL + '/users/@me/guilds').json()

        allowed = False
        for guild in guilds:
            if guild['id'] == os.environ['GUILD_ID']:
                if os.environ["ROLE_ID"]:
                    user_guild = discord.get(API_BASE_URL + f'/users/@me/guilds/{os.environ["GUILD_ID"]}/member').json()

                    roles = set(os.environ["ROLE_ID"].split(','))
                    if len(roles.intersection(set(user_guild["roles"]))) > 0:
                        allowed = True
                else:
                    allowed = True

        if allowed:
            session['uid'] = int(me['id'])
            session['username'] = me['username']
            return redirect('/home')
        else:
            flash('You are not allowed to log in', 'danger')
            return redirect('/')


@app.route('/debug')
def debug():
    if app.debug:
        session['uid'] = 1
        session['username'] = 'Debug'
        return redirect('/home')
    abort(403)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


@app.route('/discord/callback')
def callback():
    if request.values.get('error'):
        return request.values['error']
    discord = make_session(state=session.get('oauth2_state'))
    token = discord.fetch_token(
        TOKEN_URL,
        client_secret=OAUTH2_CLIENT_SECRET,
        authorization_response=request.url)
    session['oauth2_token'] = token
    return redirect('/login')


@app.route('/home', methods=['GET', 'POST'])
@is_logged_in
def home():
    if request.method == "GET":
        messages = HomeMessage.query.order_by(HomeMessage.id.asc()).all()
        for m in messages:
            m.message = markdown.markdown(m.message, extensions=MARKDOWN_EXTENSIONS)
        return render_template('home.html', messages=messages, spam_token=os.environ['SPAM_TOKEN'])
    try:
        message_id = request.form['id']

        message = HomeMessage.query.filter_by(id=message_id).first()
        if message:
            db.session.delete(message)
            db.session.commit()
    except:
        flash('Something went wrong', 'danger')
        return redirect('/home')

    flash('Message deleted', 'success')
    return redirect('/home')


@app.route('/add_ssh', methods=['GET', 'POST'])
@is_logged_in
def add_ssh():
    if request.method == "GET":
        return render_template('add_ssh.html')
    try:
        key = request.form['key'].strip()

        if '\r' in key or '\n' in key:
            flash('Your key is not allowed to contain newlines', 'danger')
            return redirect('/add_ssh')

        ssh_key = SSHKey(session['uid'], session['username'], key)
        db.session.add(ssh_key)
        db.session.commit()
    except:
        flash('Something went wrong', 'danger')
        return redirect('/add_ssh')

    flash('SSH key added', 'success')
    return redirect('/manage_ssh')


@app.route('/manage_ssh', methods=['GET', 'POST'])
@is_logged_in
def manage_ssh():
    if request.method == 'GET':
        return render_template('manage_ssh.html', keys=SSHKey.query.filter_by(uid=session['uid']).all())

    try:
        key_id = request.form['id']

        ssh_key = SSHKey.query.filter_by(id=key_id).first()
        if ssh_key and ssh_key.uid == session['uid']:
            db.session.delete(ssh_key)
            db.session.commit()
    except:
        flash('Something went wrong', 'danger')
        return redirect('/manage_ssh')

    flash('SSH key deleted', 'success')
    return redirect('/manage_ssh')


@app.route('/get_ssh')
def get_ssh():
    ret = ""

    for key in SSHKey.query.all():
        ret += f"{key.key} {key.name}\n"

    return ret, 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/add_message', methods=['GET', 'POST'])
@is_logged_in
def add_message():
    if request.method == "GET":
        return render_template('add_message.html')
    try:
        message_text = request.form['message']

        message = HomeMessage(message_text, session['username'])
        db.session.add(message)
        db.session.commit()
    except:
        traceback.print_exc()
        flash('Something went wrong', 'danger')
        return redirect('/home')

    flash('Message added', 'success')
    return redirect('/home')


@app.route('/get_wireguard')
@is_logged_in
def get_wireguard():
    config: Wireguard = Wireguard.query.filter_by(uid=session['uid']).first()
    if config:
        return send_from_directory('wireguard', config.filename)

    config_files = os.listdir('/wireguard')

    for file in config_files:
        try:
            config = Wireguard(file, session['uid'])
            db.session.add(config)
            db.session.commit()
            return send_from_directory('wireguard', file)
        except:
            db.session.rollback()
    else:
        flash('No more wireguard configs left', 'danger')
        return redirect('/home')


@app.route('/notify', methods=['POST'])
def ping():
    if not os.environ['SPAM_TOKEN'] or request.headers.get('X-ALLOW-SPAM', None) != os.environ['SPAM_TOKEN']:
        return {'err': 'invalid spam token'}, 401

    msg = request.form.get('msg', None)

    if not msg:
        return {'err': 'msg missing'}, 400

    try:
        notification = Notification(msg)
        db.session.add(notification)
        db.session.commit()
        print(notification.time)
    except:
        return {'err': 'something went wrong'}, 500

    sse.publish({'msg': notification.notification, 'time': notification.time.strftime('%a, %d %b %Y %H:%M:%S %Z')}, channel='notifications')

    if os.environ['DISCORD_WEBHOOK_URL']:
        # Trigger Discord webhook
        resp = requests.post(
            os.environ['DISCORD_WEBHOOK_URL'] + '?wait=true',
            data={"content": msg},
        )
        if resp.status_code != 200:
            return {'err': f'Discord returned code {resp.status_code}: {resp.text}'}, 500

    return {'ok': ''}, 200


@app.route('/view_notifications', methods=['GET'])
@is_logged_in
def view_notifications():
    def time_to_str(n):
        n.time = n.time.strftime('%a, %d %b %Y %H:%M:%S %Z')
        return n

    notifications = Notification.query.order_by(Notification.time.desc()).all()
    notifications = map(lambda n: time_to_str(n), notifications)
    return render_template('view_notifications.html', notifications=notifications)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=1234, threaded=True)
