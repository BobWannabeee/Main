import sqlite3
import random
import math
import logging
from datetime import datetime, timezone, timedelta
from functools import wraps
from collections import defaultdict
from flask_cors import CORS

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, g
)
from werkzeug.security import generate_password_hash, check_password_hash   #WSGI sever pronouced WHISKEY xD also hashed password


#  App setup

app = Flask(__name__, static_folder='Static', template_folder='Templates')
app.secret_key = 'lucky_strip_secret_2024_xk9mq'  # change in prod
CORS(app)  # Enable CORS for all routes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

DATABASE = 'lucky_strip.db'


# ──────────────────────────────────────────────────────────────────────────────
#  DB helpers
# ──────────────────────────────────────────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Create all tables. Safe to call repeatedly (IF NOT EXISTS guards)."""
    db = sqlite3.connect(DATABASE)
    db.executescript('''
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            wallet        REAL    NOT NULL DEFAULT 1000.0,
            total_wagered REAL    NOT NULL DEFAULT 0.0,
            total_won     REAL    NOT NULL DEFAULT 0.0,
            race_wins     INTEGER NOT NULL DEFAULT 0,
            race_plays    INTEGER NOT NULL DEFAULT 0,
            keno_wins     INTEGER NOT NULL DEFAULT 0,
            keno_plays    INTEGER NOT NULL DEFAULT 0,
            win_streak    INTEGER NOT NULL DEFAULT 0,
            best_streak   INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS race_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            horse_id      INTEGER NOT NULL,
            horse_name    TEXT    NOT NULL,
            bet           REAL    NOT NULL,
            won           INTEGER NOT NULL,
            payout        REAL    NOT NULL,
            winner_name   TEXT    NOT NULL,
            winner_place  INTEGER NOT NULL DEFAULT 1,
            mode          TEXT    NOT NULL DEFAULT 'classic',
            weather       TEXT,
            powers_used   TEXT,
            power_cost    REAL    NOT NULL DEFAULT 0,
            place_finished INTEGER NOT NULL DEFAULT 6,
            played_at     TEXT    NOT NULL
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

        CREATE TABLE IF NOT EXISTS achievements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            code        TEXT    NOT NULL,
            unlocked_at TEXT    NOT NULL,
            UNIQUE(user_id, code)
        );

        CREATE TABLE IF NOT EXISTS daily_challenges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            date        TEXT    NOT NULL,
            key         TEXT    NOT NULL,
            progress    INTEGER NOT NULL DEFAULT 0,
            completed   INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, date, key)
        );
    ''')
    db.commit()
    db.close()
    log.info('Database initialised.')


# ──────────────────────────────────────────────────────────────────────────────
#  Auth decorators
# ──────────────────────────────────────────────────────────────────────────────
def login_required(f):
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


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────────
def current_user():
    if 'user_id' not in session:
        return None
    return get_db().execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()


def now_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def today_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


# ──────────────────────────────────────────────────────────────────────────────
#  HORSES — extended with traits and form
# ──────────────────────────────────────────────────────────────────────────────
# trait: fast | stamina | lucky | erratic | steady
# true_weight: base probability weight (higher = more likely to win)
HORSES = [
    {'id': 1, 'name': 'Coran',     'odds': 4.5, 'true_weight': 6, 'trait': 'fast',    'form': 'W-W-L-W-L'},
    {'id': 2, 'name': 'Jack',      'odds': 6.0, 'true_weight': 5, 'trait': 'stamina', 'form': 'L-W-W-L-W'},
    {'id': 3, 'name': 'Harry',     'odds': 5.0, 'true_weight': 6, 'trait': 'steady',  'form': 'W-L-W-W-L'},
    {'id': 4, 'name': 'Mr Kelly',  'odds': 7.5, 'true_weight': 4, 'trait': 'lucky',   'form': 'L-L-W-L-W'},
    {'id': 5, 'name': 'Ari',       'odds': 8.0, 'true_weight': 3, 'trait': 'erratic', 'form': 'W-L-L-L-W'},
    {'id': 6, 'name': 'Joe',       'odds': 5.5, 'true_weight': 5, 'trait': 'stamina', 'form': 'L-W-L-W-W'},
]

HORSE_MAP = {h['id']: h for h in HORSES}


# ──────────────────────────────────────────────────────────────────────────────
#  KENO payout table
# ──────────────────────────────────────────────────────────────────────────────
PAYOUT_TABLE = {
    1:  {1: 3},
    2:  {1: 1, 2: 5},
    3:  {2: 2, 3: 15},
    4:  {2: 1, 3: 5, 4: 50},
    5:  {2: 1, 3: 3, 4: 20, 5: 100},
    6:  {3: 2, 4: 10, 5: 30, 6: 200},
    7:  {3: 1, 4: 7, 5: 20, 6: 100, 7: 500},
    8:  {3: 1, 4: 5, 5: 15, 6: 50, 7: 200, 8: 1000},
    9:  {3: 1, 4: 3, 5: 10, 6: 30, 7: 150, 8: 500, 9: 2000},
    10: {3: 1, 4: 2, 5: 5,  6: 20, 7: 100, 8: 400, 9: 1500, 10: 10000},
}


# ──────────────────────────────────────────────────────────────────────────────
#  WEATHER SYSTEM
# ──────────────────────────────────────────────────────────────────────────────
# Each condition has multipliers per trait affecting true_weight
WEATHER_EFFECTS = {
    'sunny': {},                                                    # no changes
    'rain':  {'stamina': 1.6, 'fast': 0.6, 'erratic': 0.5, 'lucky': 1.1, 'steady': 1.2},
    'mud':   {'stamina': 1.7, 'fast': 0.5, 'erratic': 0.4, 'lucky': 1.0, 'steady': 1.3},
    'night': {'lucky': 1.8,   'erratic': 1.3, 'fast': 0.9, 'stamina': 1.0, 'steady': 0.8},
    'windy': {'steady': 2.0,  'erratic': 0.4, 'fast': 0.7, 'stamina': 1.1, 'lucky': 0.9},
}

def apply_weather_weights(base_weights: dict, weather: str) -> dict:
    """Return new weight dict after applying weather multipliers."""
    effects = WEATHER_EFFECTS.get(weather, {})
    if not effects:
        return base_weights
    new_weights = {}
    for hid, w in base_weights.items():
        trait = HORSE_MAP[hid]['trait']
        mult  = effects.get(trait, 1.0)
        new_weights[hid] = max(0.5, w * mult)
    return new_weights


# ──────────────────────────────────────────────────────────────────────────────
#  GAME MODES
# ──────────────────────────────────────────────────────────────────────────────
VALID_MODES = {'classic', 'turbo', 'mystery', 'weather'}


# ──────────────────────────────────────────────────────────────────────────────
#  POWERS
# ──────────────────────────────────────────────────────────────────────────────
VALID_POWERS = {
    'boost',        # speed boost mid-race (visual only, slight weight +)
    'shield',       # 50% refund if 2nd place
    'ghost',        # slight debuff to nearby horses
    'oracle',       # no server effect (client shows probs)
    'sabotage',     # leader stumbles at 75% — penalise leader weight
    'insurance',    # 25% back on loss
    'double',       # 2× payout AND 2× loss
    'photofinish',  # if selected horse is 2nd and diff < threshold, count as win
    'lucky7',       # 7% random free win
}

POWER_COSTS = {
    'boost': 30, 'shield': 20, 'ghost': 35, 'oracle': 15,
    'sabotage': 50, 'insurance': 25, 'double': 0, 'photofinish': 40, 'lucky7': 60,
}


# ──────────────────────────────────────────────────────────────────────────────
#  RACE SIMULATION ENGINE (v2 — mode-aware, power-aware)
# ──────────────────────────────────────────────────────────────────────────────
def compute_effective_weights(selected_id: int, powers: list, weather: str, mode: str) -> dict:
    """
    Return a {horse_id: weight} dict after applying all modifiers:
    weather effects, mode modifiers, and power effects.
    """
    weights = {h['id']: float(h['true_weight']) for h in HORSES}

    # Weather
    if weather and weather != 'sunny':
        weights = apply_weather_weights(weights, weather)

    # Mystery mode: random per-horse multiplier (0.4x – 2.5x)
    if mode == 'mystery':
        for hid in weights:
            weights[hid] *= random.uniform(0.4, 2.5)

    # Turbo mode: more variance (erratic swings)
    if mode == 'turbo':
        for hid in weights:
            trait = HORSE_MAP[hid]['trait']
            if trait == 'erratic':
                weights[hid] *= random.uniform(0.3, 3.0)
            else:
                weights[hid] *= random.uniform(0.8, 1.4)

    # Power: ghost — slight debuff to non-selected horses
    if 'ghost' in powers:
        for hid in weights:
            if hid != selected_id:
                weights[hid] *= 0.88

    # Power: boost — buff selected horse
    if 'boost' in powers:
        weights[selected_id] *= 1.20

    # Power: lucky7 — 7% chance to set selected horse weight sky-high
    lucky7_triggered = False
    if 'lucky7' in powers and random.random() < 0.07:
        weights[selected_id] = sum(weights.values()) * 10  # guaranteed
        lucky7_triggered = True

    # Normalise weights (never negative)
    total = sum(max(0.1, w) for w in weights.values())
    weights = {hid: max(0.1, w) / total for hid, w in weights.items()}

    return weights, lucky7_triggered


def pick_winner(weights: dict) -> int:
    """Weighted random choice — returns horse id."""
    ids  = list(weights.keys())
    wts  = [weights[i] for i in ids]
    return random.choices(ids, weights=wts, k=1)[0]


def build_finish_order(winner_id: int, weights: dict) -> list:
    """Build a full 6-horse finish order consistent with weights."""
    remaining = [h for h in HORSES if h['id'] != winner_id]
    sub_weights = [weights.get(h['id'], 1.0) for h in remaining]
    finish = [HORSE_MAP[winner_id]]
    while remaining:
        chosen = random.choices(remaining, weights=sub_weights, k=1)[0]
        idx    = remaining.index(chosen)
        finish.append(chosen)
        remaining.pop(idx)
        sub_weights.pop(idx)
    return finish


def compute_place_and_gap(selected_id: int, winner_id: int, weights: dict) -> tuple:
    """
    Quickly compute the selected horse's finish place and approximate gap
    without generating a full frame-by-frame simulation.
    Uses the finish order weights to estimate positions.
    Returns (place_of_selected, gap).
    """
    finish_order = build_finish_order(winner_id, weights)
    place_of_selected = next(
        (i + 1 for i, h in enumerate(finish_order) if h['id'] == selected_id),
        len(HORSES)
    )
    # Gap: approximate based on weight difference between 1st and 2nd
    if len(finish_order) >= 2:
        w1 = weights.get(finish_order[0]['id'], 1.0)
        w2 = weights.get(finish_order[1]['id'], 1.0)
        gap = abs(w1 - w2) * 10 + random.uniform(0, 3)
    else:
        gap = 5.0
    return finish_order, place_of_selected, round(gap, 3)


def apply_power_effects(result: dict, selected_id: int, winner_id: int, powers: list,
                         bet: float, base_payout: float, place: int, gap: float,
                         lucky7_triggered: bool) -> tuple:
    """
    After the race, apply financial power effects.
    Returns (final_payout, actual_loss, power_report_string).
    """
    payout   = base_payout
    loss     = bet
    reports  = []
    won      = result['win']

    if lucky7_triggered and not won:
        # Lucky7 actually triggered but winner was forced — this is the fallback
        reports.append('🍀 Lucky Clover almost worked...')

    # Shield: 2nd place refund
    if 'shield' in powers and not won and place == 2:
        refund   = round(bet * 0.50, 2)
        loss     = round(bet - refund, 2)
        reports.append(f'🛡️ Shield activated! 50% refund: +${refund:.2f}')

    # Insurance: always 25% back on loss
    if 'insurance' in powers and not won:
        refund   = round(bet * 0.25, 2)
        loss     = round(max(0, loss - refund), 2)
        reports.append(f'🏦 Insurance paid out: +${refund:.2f}')

    # Double Down: 2× win or 2× loss
    if 'double' in powers:
        if won:
            payout  = round(payout * 2, 2)
            reports.append(f'💎 Double Down WIN: payout doubled to +${payout:.2f}')
        else:
            loss    = round(loss * 2, 2)
            reports.append(f'💎 Double Down LOSS: loss doubled to -${loss:.2f}')

    # Photo finish: if close race and selected was 2nd, count as win
    if 'photofinish' in powers and not won and place == 2 and gap < 1.5:
        payout   = round(base_payout * 0.85, 2)  # slightly reduced win
        loss     = 0.0
        reports.append(f'📸 Photo Finish! Too close to call — you win ${payout:.2f}!')

    # Sabotage visual report
    if 'sabotage' in powers:
        reports.append('💣 Sabotage planted mid-race!')

    # Ghost report
    if 'ghost' in powers:
        reports.append('👻 Ghost mode applied -12% to rivals')

    return payout, loss, ' | '.join(reports)


# ──────────────────────────────────────────────────────────────────────────────
#  BUILD FULL RACE RESPONSE  (frames generated client-side from seeds)
# ──────────────────────────────────────────────────────────────────────────────
def build_race_response(selected_id: int, bet: float, mode: str, powers: list, weather: str) -> dict:
    powers = [p for p in powers if p in VALID_POWERS]

    weights, lucky7_triggered = compute_effective_weights(selected_id, powers, weather, mode)
    winner_id = pick_winner(weights)
    finish_order, place_of_selected, gap = compute_place_and_gap(selected_id, winner_id, weights)

    won          = (winner_id == selected_id)
    winner_horse = HORSE_MAP[winner_id]
    base_payout  = round(bet * winner_horse['odds'], 2) if won else 0.0
    if mode == 'turbo' and won:
        base_payout = round(base_payout * 1.5, 2)

    # --- Send speed seeds so the client can animate without waiting for frames ---
    # Normalised weights → base speeds (same formula the old simulate used)
    total_w     = sum(weights.values())
    VARIANCE_MAP = {'fast': 0.30, 'stamina': 0.20, 'lucky': 0.45, 'erratic': 0.65, 'steady': 0.15}
    speed_seeds = {
        str(h['id']): {
            'speed':    round((weights.get(h['id'], 1.0) / total_w) * 2.4, 4),
            'variance': VARIANCE_MAP.get(h['trait'], 0.30),
            'trait':    h['trait'],
        }
        for h in HORSES
    }

    result = {
        # No 'frames' — client simulates the animation locally
        'speedSeeds':    speed_seeds,
        'finishOrder':   [{'id': h['id'], 'name': h['name']} for h in finish_order],
        'winner':        winner_horse['name'],
        'winnerId':      winner_id,
        'win':           won,
        'payout':        base_payout,
        'place':         place_of_selected,
        'gap':           gap,
        'lucky7':        lucky7_triggered,
        'selectedHorse': HORSE_MAP[selected_id],
        'mode':          mode,
        'powers':        powers,
    }

    final_payout, actual_loss, power_report = apply_power_effects(
        result, selected_id, winner_id, powers, bet, base_payout, place_of_selected, gap, lucky7_triggered
    )
    result['payout']       = final_payout
    result['actual_loss']  = actual_loss
    result['power_report'] = power_report

    if 'photofinish' in powers and not won and place_of_selected == 2 and gap < 1.5:
        result['win'] = True

    return result


# ──────────────────────────────────────────────────────────────────────────────
#  KENO RESPONSE (unchanged logic, better docs)
# ──────────────────────────────────────────────────────────────────────────────
def build_keno_response(picks: list, bet: float, side: str | None = None) -> dict:
    draw          = random.sample(range(1, 81), 10)
    matches       = sorted(set(picks) & set(draw))
    hits          = len(matches)
    base_factor   = PAYOUT_TABLE.get(len(picks), {}).get(hits, 0)
    base_payout   = round(bet * base_factor, 2) if base_factor else 0.0

    heads_count = sum(1 for n in draw if n <= 40)
    tails_count = len(draw) - heads_count
    side_win = False
    bonus = 0.0

    if side in {'H', 'T'} and hits and base_payout:
        if (side == 'H' and heads_count > tails_count) or (side == 'T' and tails_count > heads_count):
            side_win = True
            bonus = round(base_payout * 0.5, 2)

    payout      = round(base_payout + bonus, 2)
    total_factor = round(payout / bet, 4) if bet else 0.0

    return {
        'draw':          draw,
        'picks':         sorted(picks),
        'matches':       matches,
        'hits':          hits,
        'base_factor':   base_factor,
        'base_payout':   base_payout,
        'heads_count':   heads_count,
        'tails_count':   tails_count,
        'side_choice':   side,
        'side_win':      side_win,
        'bonus':         bonus,
        'payout':        payout,
        'factor':        total_factor,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  ACHIEVEMENTS ENGINE
# ──────────────────────────────────────────────────────────────────────────────
ACHIEVEMENT_DEFS = {
    'first_race':     {'name': 'Off to the Races',  'desc': 'Win your very first race', 'xp': 50},
    'streak_3':       {'name': 'Hat Trick',          'desc': '3-race win streak',        'xp': 100},
    'streak_5':       {'name': 'On Fire',            'desc': '5-race win streak',        'xp': 250},
    'big_win':        {'name': 'High Roller',        'desc': 'Win $500+ in one race',    'xp': 150},
    'lucky_7':        {'name': 'Lucky 7!',           'desc': 'Lucky Clover triggers',    'xp': 200},
    'turbo_winner':   {'name': 'Need for Speed',     'desc': 'Win a Turbo race',         'xp': 80},
    'mystery_winner': {'name': 'Dark Horse',         'desc': 'Win a Mystery race',       'xp': 80},
    'weather_winner': {'name': "Rain Man",           'desc': 'Win a Weather race',       'xp': 80},
    'photo_finish':   {'name': 'By a Nose',          'desc': 'Win via Photo Finish',     'xp': 120},
    'keno_jackpot':   {'name': 'Keno King',          'desc': '10/10 hits in Keno',       'xp': 500},
    'penny_pincher':  {'name': 'Penny Pincher',      'desc': 'Win with a $1 bet',        'xp': 60},
    'all_in':         {'name': 'All In',             'desc': 'Bet 90%+ of wallet',       'xp': 75},
    'comeback':       {'name': 'Back from the Brink','desc': 'Win after reaching <$50', 'xp': 200},
    'centurion':      {'name': 'Centurion',          'desc': 'Play 100 races',           'xp': 300},
}


def check_and_award_achievements_fast(user_id: int, context: dict, db) -> list:
    """
    Faster version of check_and_award_achievements — accepts a db connection
    and context dict that already contains the updated user stats, avoiding
    a redundant SELECT * FROM users.
    """
    existing   = {row['code'] for row in db.execute('SELECT code FROM achievements WHERE user_id=?', (user_id,)).fetchall()}
    new_awards = []

    def award(code):
        if code not in existing and code in ACHIEVEMENT_DEFS:
            db.execute('INSERT OR IGNORE INTO achievements (user_id, code, unlocked_at) VALUES (?,?,?)', (user_id, code, now_utc()))
            new_awards.append(ACHIEVEMENT_DEFS[code])
            existing.add(code)

    if context.get('won') and context.get('race_wins', 0) == 1:
        award('first_race')
    if context.get('win_streak', 0) >= 3:
        award('streak_3')
    if context.get('win_streak', 0) >= 5:
        award('streak_5')
    if context.get('payout', 0) >= 500:
        award('big_win')
    if context.get('lucky7'):
        award('lucky_7')
    mode = context.get('mode', 'classic')
    if context.get('won'):
        if mode == 'turbo':   award('turbo_winner')
        if mode == 'mystery': award('mystery_winner')
        if mode == 'weather': award('weather_winner')
    if context.get('photo_finish_win'):
        award('photo_finish')
    if context.get('keno_hits', 0) == 10 and context.get('keno_picks', 0) == 10:
        award('keno_jackpot')
    if context.get('won') and context.get('bet', 999) <= 1.0:
        award('penny_pincher')
    if context.get('wallet_before', 1000) > 0:
        if context.get('bet', 0) / context.get('wallet_before', 1000) >= 0.9:
            award('all_in')
    if context.get('won') and context.get('wallet_before', 1000) < 50:
        award('comeback')
    if context.get('race_plays', 0) >= 100:
        award('centurion')

    return new_awards


def check_and_award_achievements(user_id: int, context: dict) -> list:
    """Legacy wrapper — used by keno path which passes no db."""
    db = get_db()
    # Merge user db stats into context
    user = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    context.setdefault('win_streak', user['win_streak'])
    context.setdefault('race_wins',  user['race_wins'])
    context.setdefault('race_plays', user['race_plays'])
    return check_and_award_achievements_fast(user_id, context, db)


# ──────────────────────────────────────────────────────────────────────────────
#  DAILY CHALLENGE HELPERS (server-side tracking)
# ──────────────────────────────────────────────────────────────────────────────
DAILY_CHALLENGE_DEFS = {
    'daily_wins':        {'goal': 3,  'xp': 60},
    'turbo_wins':        {'goal': 1,  'xp': 80},
    'mystery_plays':     {'goal': 2,  'xp': 50},
    'three_power_race':  {'goal': 1,  'xp': 40},
    'streak3':           {'goal': 1,  'xp': 100},
}


def advance_daily_challenge(user_id: int, key: str, amount: int = 1):
    """Advance a daily challenge; returns True if it just completed."""
    if key not in DAILY_CHALLENGE_DEFS:
        return False
    db   = get_db()
    date = today_str()
    defn = DAILY_CHALLENGE_DEFS[key]

    db.execute('''
        INSERT INTO daily_challenges (user_id, date, key, progress, completed)
        VALUES (?,?,?,0,0) ON CONFLICT(user_id,date,key) DO NOTHING
    ''', (user_id, date, key))

    row = db.execute('SELECT * FROM daily_challenges WHERE user_id=? AND date=? AND key=?',
                     (user_id, date, key)).fetchone()
    if not row or row['completed']:
        return False

    new_prog = min(defn['goal'], row['progress'] + amount)
    completed = 1 if new_prog >= defn['goal'] else 0
    db.execute('UPDATE daily_challenges SET progress=?, completed=? WHERE user_id=? AND date=? AND key=?',
               (new_prog, completed, user_id, date, key))
    return completed == 1


# ──────────────────────────────────────────────────────────────────────────────
#  ROUTES — Pages
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def home():
    return render_template('Index.html', user=current_user())


@app.route('/race')
@login_required
def race():
    return render_template('Race.html', horses=HORSES, user=current_user())


@app.route('/keno')
@login_required
def keno():
    return render_template('Keno.html', user=current_user())


@app.route('/profile')
@login_required
def profile():
    user  = current_user()
    db    = get_db()
    races = db.execute(
        'SELECT * FROM race_history WHERE user_id=? ORDER BY played_at DESC LIMIT 20',
        (user['id'],)
    ).fetchall()
    kenos = db.execute(
        'SELECT * FROM keno_history WHERE user_id=? ORDER BY played_at DESC LIMIT 20',
        (user['id'],)
    ).fetchall()
    race_stats = db.execute(
        '''SELECT COUNT(*) as total, SUM(won) as wins,
                  SUM(bet) as wagered, SUM(payout) as returned
           FROM race_history WHERE user_id=?''',
        (user['id'],)
    ).fetchone()
    keno_stats = db.execute(
        '''SELECT COUNT(*) as total, SUM(won) as wins,
                  SUM(bet) as wagered, SUM(payout) as returned
           FROM keno_history WHERE user_id=?''',
        (user['id'],)
    ).fetchone()
    user_achievements = db.execute(
        'SELECT code, unlocked_at FROM achievements WHERE user_id=? ORDER BY unlocked_at DESC',
        (user['id'],)
    ).fetchall()
    enriched_achievements = [
        {**ACHIEVEMENT_DEFS.get(row['code'], {'name': row['code'], 'desc': '', 'xp': 0}),
         'code': row['code'], 'unlocked_at': row['unlocked_at']}
        for row in user_achievements
    ]
    today_challenges = db.execute(
        'SELECT key, progress, completed FROM daily_challenges WHERE user_id=? AND date=?',
        (user['id'], today_str())
    ).fetchall()

    return render_template(
        'Profile.html',
        user=user,
        races=races,
        kenos=kenos,
        race_stats=race_stats,
        keno_stats=keno_stats,
        achievements=enriched_achievements,
        daily_challenges=today_challenges,
        challenge_defs=DAILY_CHALLENGE_DEFS,
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Auth routes
# ──────────────────────────────────────────────────────────────────────────────
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
        elif len(username) < 3 or len(username) > 20:
            error = 'Username must be 3–20 characters.'
        else:
            db = get_db()
            if db.execute('SELECT id FROM users WHERE username=? OR email=?', (username, email)).fetchone():
                error = 'Username or email already taken.'
            else:
                db.execute(
                    '''INSERT INTO users
                       (username, email, password_hash, wallet, created_at)
                       VALUES (?,?,?,?,?)''',
                    (username, email, generate_password_hash(password), 1000.0, now_utc())
                )
                db.commit()
                user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
                session['user_id'] = user['id']
                log.info('New user registered: %s', username)
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
            log.info('User logged in: %s', username)
            return redirect(url_for('home'))
    return render_template('Login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ──────────────────────────────────────────────────────────────────────────────
#  API — Wallet
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/wallet')
@api_login_required
def api_wallet():
    return jsonify({'wallet': current_user()['wallet']})


@app.route('/api/wallet/buyin', methods=['POST'])
@api_login_required
def api_wallet_buyin():
    """Top-up wallet when player runs low. Max 3 top-ups per session."""
    topups = session.get('topup_count', 0)
    if topups >= 3:
        return jsonify({'error': 'Max 3 top-ups per session. Come back later!'}), 400
    amount = float(request.get_json(force=True, silent=True).get('amount', 500))
    amount = min(max(amount, 100), 1000)
    user   = current_user()
    db     = get_db()
    new_wallet = round(user['wallet'] + amount, 2)
    db.execute('UPDATE users SET wallet=? WHERE id=?', (new_wallet, user['id']))
    db.commit()
    session['topup_count'] = topups + 1
    return jsonify({'wallet': new_wallet, 'topups_remaining': 3 - session['topup_count']})


# ──────────────────────────────────────────────────────────────────────────────
#  API — RACE (v2)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/race', methods=['POST'])
@api_login_required
def api_race():
    data     = request.get_json(force=True, silent=True) or {}
    selected = int(data.get('horse', 0))
    bet      = float(data.get('bet', 0))
    mode     = str(data.get('mode', 'classic')).lower()
    powers   = list(data.get('powers', []))
    weather  = str(data.get('weather', '') or 'sunny').lower()
    user     = current_user()
    db       = get_db()

    # ── Validation ────────────────────────────────────────────────────────────
    if selected not in HORSE_MAP:
        return jsonify({'error': 'Select a valid horse.'}), 400
    if bet <= 0:
        return jsonify({'error': 'Enter a positive bet.'}), 400
    if mode not in VALID_MODES:
        mode = 'classic'
    if weather not in WEATHER_EFFECTS:
        weather = 'sunny'

    # Validate & cost powers
    powers      = [p for p in powers if p in VALID_POWERS]
    power_cost  = sum(POWER_COSTS.get(p, 0) for p in powers)
    total_debit = bet + power_cost

    if total_debit > user['wallet']:
        return jsonify({'error': f'Not enough! Bet ${bet:.2f} + powers ${power_cost:.2f} = ${total_debit:.2f}'}), 400

    # ── Run race ──────────────────────────────────────────────────────────────
    wallet_before = user['wallet']
    result        = build_race_response(selected, bet, mode, powers, weather)
    won           = result['win']
    payout        = result['payout']
    actual_loss   = result.get('actual_loss', bet)

    # Net wallet change
    if won:
        net = payout - bet - power_cost
    else:
        net = -(actual_loss + power_cost)

    new_wallet = round(user['wallet'] + net, 2)
    new_wallet = max(0.0, new_wallet)

    # ── DB writes — all in one transaction ───────────────────────────────────
    place = result.get('place', 6)

    # Update user stats; compute new streak inline to avoid re-fetch
    new_streak = (user['win_streak'] + 1) if won else 0
    new_best   = max(user['best_streak'], new_streak)

    db.execute('''
        UPDATE users SET
            wallet        = ?,
            total_wagered = total_wagered + ?,
            total_won     = total_won + ?,
            race_plays    = race_plays + 1,
            race_wins     = race_wins + ?,
            win_streak    = ?,
            best_streak   = ?
        WHERE id=?
    ''', (
        new_wallet,
        total_debit, payout,
        1 if won else 0,
        new_streak, new_best,
        user['id']
    ))

    db.execute('''
        INSERT INTO race_history
            (user_id, horse_id, horse_name, bet, won, payout, winner_name,
             winner_place, mode, weather, powers_used, power_cost, place_finished, played_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        user['id'], selected,
        HORSE_MAP[selected]['name'],
        bet, 1 if won else 0, payout,
        result['winner'], 1, mode,
        weather if weather != 'sunny' else None,
        ','.join(powers) if powers else None,
        power_cost, place, now_utc()
    ))

    # ── Achievements (reuse user data we already have) ────────────────────────
    context = {
        'won': won, 'bet': bet, 'payout': payout, 'mode': mode,
        'lucky7': result.get('lucky7', False),
        'photo_finish_win': ('photofinish' in powers and place == 2 and result.get('gap', 99) < 1.5),
        'wallet_before': wallet_before,
        'win_streak': new_streak,
        'race_wins':  (user['race_wins'] + (1 if won else 0)),
        'race_plays': (user['race_plays'] + 1),
    }
    new_achievements = check_and_award_achievements_fast(user['id'], context, db)

    # ── Daily challenges ──────────────────────────────────────────────────────
    completed_challenges = []
    if won:
        if advance_daily_challenge(user['id'], 'daily_wins'): completed_challenges.append('daily_wins')
        if mode == 'turbo':
            if advance_daily_challenge(user['id'], 'turbo_wins'): completed_challenges.append('turbo_wins')
    if mode == 'mystery':
        if advance_daily_challenge(user['id'], 'mystery_plays'): completed_challenges.append('mystery_plays')
    if len(powers) >= 3:
        if advance_daily_challenge(user['id'], 'three_power_race'): completed_challenges.append('three_power_race')
    if new_streak >= 3:
        if advance_daily_challenge(user['id'], 'streak3'): completed_challenges.append('streak3')

    db.commit()  # single commit for everything above

    # ── Build response ────────────────────────────────────────────────────────
    result['wallet']              = new_wallet
    result['power_cost']          = power_cost
    result['streak']              = new_streak
    result['best_streak']         = new_best
    result['new_achievements']    = new_achievements
    result['completed_challenges']= completed_challenges
    result['mode']                = mode

    log.info('[RACE] user=%s horse=%s mode=%s bet=%.2f won=%s payout=%.2f powers=%s',
             user['id'], selected, mode, bet, won, payout, powers)

    return jsonify(result)


# ──────────────────────────────────────────────────────────────────────────────
#  API — KENO
# ──────────────────────────────────────────────────────────────────────────────
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

    side = str(data.get('side', '') or '').upper()
    if side not in {'H', 'T'}:
        return jsonify({'error': 'Choose Heads or Tails before drawing.'}), 400

    result     = build_keno_response(picks, bet, side)
    new_wallet = round(user['wallet'] - bet + result['payout'], 2)
    won        = result['payout'] > 0

    db.execute('UPDATE users SET wallet=?, total_wagered=total_wagered+?, total_won=total_won+?, keno_plays=keno_plays+1, keno_wins=keno_wins+? WHERE id=?',
               (new_wallet, bet, result['payout'], 1 if won else 0, user['id']))
    db.execute('''
        INSERT INTO keno_history (user_id, picks, bet, hits, won, payout, factor, played_at)
        VALUES (?,?,?,?,?,?,?,?)
    ''', (user['id'], ','.join(str(p) for p in picks), bet,
          result['hits'], 1 if won else 0, result['payout'], result['factor'], now_utc()))
    db.commit()

    # Achievements
    context = {'keno_hits': result['hits'], 'keno_picks': len(picks), 'won': won, 'bet': bet, 'payout': result['payout'], 'wallet_before': user['wallet']}
    new_achievements = check_and_award_achievements(user['id'], context)
    db.commit()

    result['wallet']           = new_wallet
    result['new_achievements'] = new_achievements

    log.info('[KENO] user=%s picks=%d hits=%d side=%s side_win=%s bet=%.2f payout=%.2f',
             user['id'], len(picks), result['hits'], result['side_choice'], result['side_win'], bet, result['payout'])

    return jsonify(result)


# ──────────────────────────────────────────────────────────────────────────────
#  API — Leaderboard
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/leaderboard')
@api_login_required
def api_leaderboard():
    db   = get_db()
    rows = db.execute('''
        SELECT username,
               wallet,
               race_wins,
               race_plays,
               best_streak,
               ROUND((total_won - total_wagered), 2) as net_pl
        FROM users
        ORDER BY wallet DESC
        LIMIT 15
    ''').fetchall()
    return jsonify({'leaderboard': [dict(r) for r in rows]})


# ──────────────────────────────────────────────────────────────────────────────
#  API — Stats (detailed user stats)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/stats')
@api_login_required
def api_stats():
    user = current_user()
    db   = get_db()

    # Mode breakdown
    mode_stats = db.execute('''
        SELECT mode,
               COUNT(*) as plays,
               SUM(won) as wins,
               SUM(bet) as wagered,
               SUM(payout) as returned
        FROM race_history WHERE user_id=?
        GROUP BY mode
    ''', (user['id'],)).fetchall()

    # Best single race
    best_race = db.execute('''
        SELECT horse_name, payout, mode, played_at
        FROM race_history WHERE user_id=? AND won=1
        ORDER BY payout DESC LIMIT 1
    ''', (user['id'],)).fetchone()

    # Power usage count
    power_counts = defaultdict(int)
    power_rows   = db.execute('SELECT powers_used FROM race_history WHERE user_id=? AND powers_used IS NOT NULL', (user['id'],)).fetchall()
    for row in power_rows:
        for p in row['powers_used'].split(','):
            if p.strip():
                power_counts[p.strip()] += 1

    achievements_count = db.execute('SELECT COUNT(*) FROM achievements WHERE user_id=?', (user['id'],)).fetchone()[0]

    return jsonify({
        'wallet':          user['wallet'],
        'race_wins':       user['race_wins'],
        'race_plays':      user['race_plays'],
        'keno_wins':       user['keno_wins'],
        'keno_plays':      user['keno_plays'],
        'win_streak':      user['win_streak'],
        'best_streak':     user['best_streak'],
        'net_pl':          round(user['total_won'] - user['total_wagered'], 2),
        'mode_breakdown':  [dict(r) for r in mode_stats],
        'best_race':       dict(best_race) if best_race else None,
        'power_usage':     dict(power_counts),
        'achievements':    achievements_count,
    })


# ──────────────────────────────────────────────────────────────────────────────
#  API — Achievements
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/achievements')
@api_login_required
def api_achievements():
    user = current_user()
    db   = get_db()
    rows = db.execute('SELECT code, unlocked_at FROM achievements WHERE user_id=? ORDER BY unlocked_at DESC',
                      (user['id'],)).fetchall()
    unlocked = [{**ACHIEVEMENT_DEFS.get(r['code'], {}), 'code': r['code'], 'unlocked_at': r['unlocked_at']} for r in rows]
    locked   = [{'code': c, **d} for c, d in ACHIEVEMENT_DEFS.items() if c not in {r['code'] for r in rows}]
    return jsonify({'unlocked': unlocked, 'locked': locked})


# ──────────────────────────────────────────────────────────────────────────────
#  API — Daily Challenges status
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/challenges')
@api_login_required
def api_challenges():
    user = current_user()
    db   = get_db()
    rows = db.execute('SELECT key, progress, completed FROM daily_challenges WHERE user_id=? AND date=?',
                      (user['id'], today_str())).fetchall()
    result = []
    for key, defn in DAILY_CHALLENGE_DEFS.items():
        row = next((r for r in rows if r['key'] == key), None)
        result.append({
            'key':       key,
            'goal':      defn['goal'],
            'xp':        defn['xp'],
            'progress':  row['progress'] if row else 0,
            'completed': bool(row['completed']) if row else False,
        })
    return jsonify({'challenges': result, 'date': today_str()})


# ──────────────────────────────────────────────────────────────────────────────
#  API — Race history with filters
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/history')
@api_login_required
def api_history():
    user  = current_user()
    db    = get_db()
    mode  = request.args.get('mode', '')
    limit = min(int(request.args.get('limit', 20)), 50)

    query  = 'SELECT * FROM race_history WHERE user_id=?'
    params = [user['id']]
    if mode and mode in VALID_MODES:
        query  += ' AND mode=?'
        params.append(mode)
    query += ' ORDER BY played_at DESC LIMIT ?'
    params.append(limit)

    rows = db.execute(query, params).fetchall()
    return jsonify({'history': [dict(r) for r in rows]})


# ──────────────────────────────────────────────────────────────────────────────
#  API — Weather info (for current/next race)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/weather')
@api_login_required
def api_weather():
    """Return a random weather condition for the UI to display."""
    conds  = list(WEATHER_EFFECTS.keys())
    chosen = random.choice(conds)
    icons  = {'sunny':'☀️','rain':'🌧️','mud':'🌫️','night':'🌙','windy':'💨'}
    labels = {
        'sunny': 'Sunny & Dry — all horses at full ability',
        'rain':  'Heavy Rain — stamina horses favoured',
        'mud':   'Muddy Track — erratic horses struggle',
        'night': 'Night Race — lucky horses gain edge',
        'windy': 'Strong Crosswinds — steady horses shine',
    }
    effects = WEATHER_EFFECTS.get(chosen, {})
    return jsonify({
        'condition': chosen,
        'icon':      icons.get(chosen, '🌤️'),
        'label':     labels.get(chosen, chosen),
        'effects':   effects,
    })


# ──────────────────────────────────────────────────────────────────────────────
#  API — Power info
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/powers')
@api_login_required
def api_powers():
    powers_info = [
        {
            'id':       p,
            'cost':     POWER_COSTS.get(p, 0),
        }
        for p in VALID_POWERS
    ]
    return jsonify({'powers': powers_info})


# ──────────────────────────────────────────────────────────────────────────────
#  API — Horses info (for dynamic UI)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/horses')
@api_login_required
def api_horses():
    return jsonify({'horses': HORSES})


# ──────────────────────────────────────────────────────────────────────────────
#  Ad routes (preserved from original)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/api/ads/earn', methods=['POST'])
@api_login_required
def api_ads_earn():
    data   = request.get_json(force=True, silent=True) or {}
    amount = float(data.get('amount', 0))
    if amount <= 0 or amount > 100:
        return jsonify({'error': 'Invalid amount'}), 400
    user = current_user()
    db   = get_db()
    new_wallet = round(user['wallet'] + amount, 2)
    db.execute('UPDATE users SET wallet=? WHERE id=?', (new_wallet, user['id']))
    db.commit()
    return jsonify({'wallet': new_wallet})


@app.route('/api/ads/penalty', methods=['POST'])
@api_login_required
def api_ads_penalty():
    data   = request.get_json(force=True, silent=True) or {}
    amount = float(data.get('amount', 400))
    if amount <= 0:
        return jsonify({'error': 'Invalid amount'}), 400
    user = current_user()
    db   = get_db()
    new_wallet = round(max(0.0, user['wallet'] - amount), 2)
    db.execute('UPDATE users SET wallet=? WHERE id=?', (new_wallet, user['id']))
    session['ad_penalty'] = True
    db.commit()
    return jsonify({'wallet': new_wallet})


@app.route('/api/ads/disable', methods=['POST'])
@api_login_required
def api_ads_disable():
    session.pop('ad_penalty', None)
    return jsonify({'ok': True})


# ──────────────────────────────────────────────────────────────────────────────
#  Error handlers
# ──────────────────────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(e):
    log.exception('Internal server error')
    return jsonify({'error': 'Internal server error'}), 500


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)