from inspect import trace
import os
from discord.ext.tasks import loop
from multiprocessing import Process, Manager
from flask import Flask, session, redirect, request, abort, render_template, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from requests_oauthlib import OAuth2Session
import discord
import traceback
from functools import wraps
from secret import SECRET

app = Flask(__name__, static_url_path='/static')
app.debug = True
app.config['SECRET_KEY'] = SECRET
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
db = SQLAlchemy(app)

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

db.create_all()

### Discord OAUTH ###

OAUTH2_CLIENT_ID = os.environ['OAUTH2_CLIENT_ID']
OAUTH2_CLIENT_SECRET = os.environ['OAUTH2_CLIENT_SECRET']
OAUTH2_REDIRECT_URI = 'https://defcon.redrocket.club/discord/callback'

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
            abort(403)
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
            'identify guilds')
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
                allowed = True

        if allowed:
            session['uid'] = int(me['id'])
            session['username'] = me['username']
            return redirect('/home')
        else:
            flash('You are not allowed to log in', 'danger')
            return redirect('/')

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

@app.route('/home')
@is_logged_in
def home():
    return render_template('home.html')

@app.route('/add_ssh', methods=['GET', 'POST'])
@is_logged_in
def add_ssh():
    if request.method == "GET":
        return render_template('add_ssh.html')
    try:
        key = request.form['key']

        if not key.startswith('ssh-') or '\r' in key or '\n' in key:
            flash('This does not look like a valid SSH key', 'danger')
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

@app.route('/get_wireguard')
@is_logged_in
def get_wireguard():
    config: Wireguard = Wireguard.query.filter_by(uid=session['uid']).first()
    if config:
        return send_from_directory('wireguard', config.filename)

    config_files = os.listdir('./wireguard')

    for file in config_files:
        try:
            config = Wireguard(file, session['uid'])
            db.session.add(config)
            db.session.commit()
            return send_from_directory('wireguard', file)
        except:
            continue
    else:
        flash('No more wireguard configs left', 'danger')
        return redirect('/home')

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=1234)