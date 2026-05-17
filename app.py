import sqlite3    
import random 
from datetime import datetime, timezone     #this is for the date created
from functools import wraps  #Ai said tis usefull

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, g
)
from werkzeug.security import generate_password_hash, check_password_hash   #this ting hashes the password then just gets rid of it

app = Flask(__name__, static_folder='Static', template_folder='Templates')     #app is = flassk


app.secret_key = 'bob'

DATABASE = 'lucky_strip.db'      #database = exist


def get_db():  
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row   # access columns by name not teh index
    return g.db


@app.teardown_appcontext
def close_db(error):   #Closes it
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db(): #creats the tables
    db = sqlite3.connect(DATABASE)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            wallet        REAL    NOT NULL DEFAULT 1000.0,
            created_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS race_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            horse_id    INTEGER NOT NULL,
            horse_name  TEXT    NOT NULL,
            bet         REAL    NOT NULL,
            won         INTEGER NOT NULL,
            payout      REAL    NOT NULL,
            winner_name TEXT    NOT NULL,
            played_at   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS keno_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            picks      TEXT    NOT NULL,
            bet        REAL    NOT NULL,
            hits       INTEGER NOT NULL,
            won        INTEGER NOT NULL,
            payout     REAL    NOT NULL,
            factor     REAL    NOT NULL,
            played_at  TEXT    NOT NULL
        );
    ''')
    db.commit()
    db.close()



def login_required(f):    #fragile ai thing
    """Redirect to /login if the user isn't signed in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not logged in'}), 401
        return f(*args, **kwargs)
    return decorated


def current_user():     
    if 'user_id' not in session:
        return None
    return get_db().execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()


def now_utc():   #this is where time is used
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


#HOOOOOOOOOOOOOOORSES
HORSES = [              
    {'id': 1, 'name': 'Coran',    'odds': 4.5, 'true_weight': 6},
    {'id': 2, 'name': 'Jack',  'odds': 6.0, 'true_weight': 5},
    {'id': 3, 'name': 'Harry', 'odds': 5.0, 'true_weight': 6},
    {'id': 4, 'name': 'Mr Kelly',  'odds': 7.5, 'true_weight': 4},
    {'id': 5, 'name': 'Ari',   'odds': 8.0, 'true_weight': 3},
    {'id': 6, 'name': 'Joe',  'odds': 5.5, 'true_weight': 5},
]
#is there a better way to do this probly  but it works 
PAYOUT_TABLE = {
    1:  {1: 3},
    2:  {1: 1,  2: 5},
    3:  {1: 0,  2: 2,  3: 15},
    4:  {1: 0,  2: 1,  3: 5,   4: 50},
    5:  {1: 0,  2: 1,  3: 3,   4: 20,  5: 100},
    6:  {1: 0,  2: 0,  3: 2,   4: 10,  5: 30,  6: 200},
    7:  {1: 0,  2: 0,  3: 1,   4: 7,   5: 20,  6: 100,  7: 500},
    8:  {1: 0,  2: 0,  3: 1,   4: 5,   5: 15,  6: 50,   7: 200,  8: 1000},
    9:  {1: 0,  2: 0,  3: 1,   4: 3,   5: 10,  6: 30,   7: 150,  8: 500,  9: 2000},
    10: {1: 0,  2: 0,  3: 1,   4: 2,   5: 5,   6: 20,   7: 100,  8: 400,  9: 1500, 10: 10000},
}


def pick_rigged_winner(selected_id):
    # Fair race - winner chosen purely by true_weight odds
    weights = [h['true_weight'] for h in HORSES]
    return random.choices(HORSES, weights=weights, k=1)[0]


def simulate_race_frames(winner_id):
    """Run physics sim. Returns (frames, finish_order sorted by actual crossing time)."""
    total  = sum(h['true_weight'] for h in HORSES)
    speeds = {h['id']: (h['true_weight'] / total) * 2.2 for h in HORSES}
    positions = {h['id']: 0.0 for h in HORSES}
    frames, finished, place = [], {}, 1

    for _ in range(400):
        lead = max(positions.values())
        for h in HORSES:
            if h['id'] in finished:
                continue
            spd = speeds[h['id']]
            # Nudge winner to front when pack is past 55%
            if lead > 55 and h['id'] == winner_id:
                spd *= 1.22
            positions[h['id']] = min(100.0, positions[h['id']] + max(0.0, random.gauss(spd, 0.4)))
            if positions[h['id']] >= 100.0:
                finished[h['id']] = place
                place += 1
        frames.append({h['id']: round(positions[h['id']], 2) for h in HORSES})
        if len(finished) == len(HORSES):
            break

    # Sort by actual simulated finish position — this is what the player saw on screen
    finish_order = sorted(HORSES, key=lambda h: finished.get(h['id'], 999))
    return frames, finish_order


def build_race_response(selected_id, bet):
    winner               = pick_rigged_winner(selected_id)
    frames, finish_order = simulate_race_frames(winner['id'])
    won                  = (winner['id'] == selected_id)
    payout               = round(bet * winner['odds'], 2) if won else 0.0
    return {
        'frames':        frames,
        'finishOrder':   [{'id': h['id'], 'name': h['name']} for h in finish_order],
        'winner':        winner['name'],
        'winnerId':      winner['id'],
        'win':           won,
        'payout':        payout,
        'selectedHorse': next(h for h in HORSES if h['id'] == selected_id),
    }


def build_keno_response(picks, bet):
    draw = random.sample(range(1, 81), 20)
    matches = sorted(set(picks) & set(draw))
    hits = len(matches)
    factor = PAYOUT_TABLE.get(len(picks), {}).get(hits, 0)
    return {'draw': draw, 'picks': sorted(picks), 'matches': matches,
            'hits': hits, 'payout': round(bet * factor, 2), 'factor': factor}


#the routtttttttttes
@app.route('/')
@login_required
def home():
    user = current_user()
    new_wallet, penalised = check_ad_penalty(user)
    if penalised:
        # Re-fetch user with updated wallet for template
        user = get_db().execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
    return render_template('Index.html', user=user, ad_penalty=penalised)


@app.route('/race')
@login_required
def race():
    user = current_user()
    new_wallet, penalised = check_ad_penalty(user)
    if penalised:
        user = get_db().execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
    return render_template('Race.html', horses=HORSES, user=user, ad_penalty=penalised)


@app.route('/keno')
@login_required
def keno():
    user = current_user()
    new_wallet, penalised = check_ad_penalty(user)
    if penalised:
        user = get_db().execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
    return render_template('Keno.html', user=user, ad_penalty=penalised)


@app.route('/profile')
@login_required
def profile():
    user = current_user()
    db   = get_db()
    races = db.execute(
        'SELECT * FROM race_history WHERE user_id=? ORDER BY played_at DESC LIMIT 20',
        (user['id'],)
    ).fetchall()
    kenos = db.execute(
        'SELECT * FROM keno_history WHERE user_id=? ORDER BY played_at DESC LIMIT 20',
        (user['id'],)
    ).fetchall()
    race_stats = db.execute(
        'SELECT COUNT(*) as total, SUM(won) as wins, SUM(bet) as wagered, SUM(payout) as returned FROM race_history WHERE user_id=?',
        (user['id'],)
    ).fetchone()
    keno_stats = db.execute(
        'SELECT COUNT(*) as total, SUM(won) as wins, SUM(bet) as wagered, SUM(payout) as returned FROM keno_history WHERE user_id=?',
        (user['id'],)
    ).fetchone()
    return render_template('Profile.html', user=user, races=races, kenos=kenos,
                           race_stats=race_stats, keno_stats=keno_stats)


#this is the authentication tingies

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'user_id' in session:
        return redirect(url_for('home'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not username or not email or not password:
            error = 'All fields are required.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters.'
        else:
            db = get_db()
            if db.execute('SELECT id FROM users WHERE username=? OR email=?', (username, email)).fetchone():
                error = 'Username or email already taken.'
            else:
                db.execute(
                    'INSERT INTO users (username, email, password_hash, wallet, created_at) VALUES (?,?,?,?,?)',
                    (username, email, generate_password_hash(password), 1000.0, now_utc())
                )
                db.commit()
                user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
                session['user_id'] = user['id']
                return redirect(url_for('home'))
    return render_template('Signup.html', error=error)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('home'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = get_db().execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not user or not check_password_hash(user['password_hash'], password):
            error = 'Invalid username or password.'
        else:
            session['user_id'] = user['id']
            return redirect(url_for('home'))
    return render_template('Login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))



#API   
@app.route('/api/wallet')
@api_login_required
def api_wallet():
    return jsonify({'wallet': current_user()['wallet']})


@app.route('/api/race', methods=['POST'])
@api_login_required
def api_race():
    data     = request.get_json(force=True, silent=True) or {}
    selected = int(data.get('horse', 0))
    bet      = float(data.get('bet', 0))
    user     = current_user()
    db       = get_db()

    if selected not in [h['id'] for h in HORSES] or bet <= 0:
        return jsonify({'error': 'Select a valid horse and a positive bet.'}), 400
    if bet > user['wallet']:
        return jsonify({'error': "Not enough in your wallet."}), 400

    result     = build_race_response(selected, bet)
    new_wallet = round(user['wallet'] - bet + result['payout'], 2)

    db.execute('UPDATE users SET wallet=? WHERE id=?', (new_wallet, user['id']))
    db.execute(
        'INSERT INTO race_history (user_id,horse_id,horse_name,bet,won,payout,winner_name,played_at) VALUES (?,?,?,?,?,?,?,?)',
        (user['id'], selected,
         next(h['name'] for h in HORSES if h['id'] == selected),
         bet, 1 if result['win'] else 0, result['payout'], result['winner'], now_utc())
    )
    db.commit()

    result['wallet'] = new_wallet
    return jsonify(result)


@app.route('/api/keno', methods=['POST'])
@api_login_required
def api_keno():
    data  = request.get_json(force=True, silent=True) or {}
    picks = data.get('picks', [])
    bet   = float(data.get('bet', 0))
    user  = current_user()
    db    = get_db()

    if not isinstance(picks, list):
        return jsonify({'error': 'Picks must be a list.'}), 400
    picks = sorted({int(n) for n in picks if 1 <= int(n) <= 80})
    if not (1 <= len(picks) <= 10):
        return jsonify({'error': 'Pick 1–10 numbers.'}), 400
    if bet <= 0:
        return jsonify({'error': 'Enter a positive bet.'}), 400
    if bet > user['wallet']:
        return jsonify({'error': "Not enough in your wallet."}), 400

    result     = build_keno_response(picks, bet)
    new_wallet = round(user['wallet'] - bet + result['payout'], 2)

    db.execute('UPDATE users SET wallet=? WHERE id=?', (new_wallet, user['id']))
    db.execute(
        'INSERT INTO keno_history (user_id,picks,bet,hits,won,payout,factor,played_at) VALUES (?,?,?,?,?,?,?,?)',
        (user['id'], ','.join(str(p) for p in picks), bet,
         result['hits'], 1 if result['payout'] > 0 else 0,
         result['payout'], result['factor'], now_utc())
    )
    db.commit()

    result['wallet'] = new_wallet
    return jsonify(result)


@app.route('/api/ads/earn', methods=['POST'])
@api_login_required
def api_ads_earn():
    """Passive income from watching terrible popup ads. Each ad = 10 coins/min."""
    data   = request.get_json(force=True, silent=True) or {}
    amount = float(data.get('amount', 0))
    amount = max(0.0, min(round(amount, 2), 60.0))
    if amount <= 0:
        return jsonify({'error': 'No amount specified.'}), 400
    # Mark ads as active in session so we can penalise page-switchers
    session['ads_active'] = True
    user = current_user()
    db   = get_db()
    new_wallet = round(user['wallet'] + amount, 2)
    db.execute('UPDATE users SET wallet=? WHERE id=?', (new_wallet, user['id']))
    db.commit()
    return jsonify({'wallet': new_wallet, 'earned': amount})


@app.route('/api/ads/disable', methods=['POST'])
@api_login_required
def api_ads_disable():
    """Called when player intentionally disables ads — clears the session flag cleanly."""
    session.pop('ads_active', None)
    return jsonify({'ok': True})


@app.route('/api/ads/penalty', methods=['POST'])
@api_login_required
def api_ads_penalty():
    """Early termination fee — charged when ads were active but player bailed."""
    data   = request.get_json(force=True, silent=True) or {}
    amount = float(data.get('amount', 400))
    amount = max(0.0, min(round(amount, 2), 400.0))
    session.pop('ads_active', None)
    user   = current_user()
    db     = get_db()
    new_wallet = round(max(0.0, user['wallet'] - amount), 2)
    db.execute('UPDATE users SET wallet=? WHERE id=?', (new_wallet, user['id']))
    db.commit()
    return jsonify({'wallet': new_wallet, 'penalised': amount})


def check_ad_penalty(user):
    """Call at the start of any page load — if ads were active and player navigated away, charge them."""
    if session.get('ads_active'):
        session.pop('ads_active', None)
        db = get_db()
        new_wallet = round(max(0.0, user['wallet'] - 400.0), 2)
        db.execute('UPDATE users SET wallet=? WHERE id=?', (new_wallet, user['id']))
        db.commit()
        return new_wallet, True   # (new_wallet, was_penalised)
    return user['wallet'], False


if __name__ == '__main__':    #start the app (coppied from that one document to-do-app)
    init_db()
    app.run(debug=True, port=5000)