const raceForm = document.querySelector('#race-form');
const kenoForm = document.querySelector('#keno-form');
const raceResult = document.getElementById('race-result');
const kenoResult = document.getElementById('keno-result');
const raceTrack = document.getElementById('race-track');
const raceStage = document.getElementById('race-stage');
const raceStatus = document.getElementById('race-status');
const installButton = document.getElementById('btn-install');
const balanceEls = document.querySelectorAll('#wallet-balance');
const WALLET_KEY = 'raceKenoWallet';
const DEFAULT_BALANCE = 1000;
let deferredPrompt = null;

function formatMoney(value) {
    return Number(value).toFixed(2);
}

function getWalletBalance() {
    const stored = localStorage.getItem(WALLET_KEY);
    const parsed = parseFloat(stored);
    return Number.isFinite(parsed) ? parsed : DEFAULT_BALANCE;
}

function setWalletBalance(value) {
    localStorage.setItem(WALLET_KEY, formatMoney(value));
    updateWalletDisplay();
}

function updateWalletDisplay() {
    const balance = formatMoney(getWalletBalance());
    balanceEls.forEach((el) => {
        el.textContent = balance;
    });
}

function changeBalance(delta) {
    const next = getWalletBalance() + delta;
    setWalletBalance(next);
    return next;
}

function showMessage(container, html) {
    if (container) container.innerHTML = html;
}

async function postJson(url, payload) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Request failed');
    return data;
}

function buildRaceTrack(horses) {
    if (!raceTrack) return;
    raceTrack.innerHTML = '';
    horses.forEach((horse) => {
        const lane = document.createElement('div');
        lane.className = 'horse-lane';

        const label = document.createElement('div');
        label.className = 'track-label';
        label.textContent = horse.name;

        const runner = document.createElement('div');
        runner.className = 'runner';
        runner.textContent = '🐎';
        runner.dataset.horseId = horse.id;

        lane.appendChild(label);
        lane.appendChild(runner);
        raceTrack.appendChild(lane);
    });
}

function animateRace(order) {
    if (!raceTrack || !raceStage || !raceStatus) return;
    raceStage.classList.remove('hidden');
    raceStatus.textContent = 'The race is underway...';
    buildRaceTrack(order);

    const runners = Array.from(raceTrack.querySelectorAll('.runner'));
    runners.forEach((runner, index) => {
        const duration = 3 + index * 0.35;
        runner.style.transition = `transform ${duration}s ease-out`;
        runner.style.transform = 'translateX(calc(100% - 120px))';
    });

    const finishTime = 3500 + order.length * 350;
    setTimeout(() => {
        raceStatus.innerHTML = `<strong>Winner:</strong> ${order[0].name}`;
    }, finishTime);
}

if (raceForm) {
    if (getWalletBalance() === DEFAULT_BALANCE) {
        setWalletBalance(DEFAULT_BALANCE);
    } else {
        updateWalletDisplay();
    }

    raceForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const horse = Number(document.getElementById('horse-select').value);
        const bet = Number(document.getElementById('race-bet').value);

        if (bet <= 0) {
            showMessage(raceResult, '<p class="error">Enter a positive bet amount.</p>');
            return;
        }

        if (bet > getWalletBalance()) {
            showMessage(raceResult, '<p class="error">Not enough balance to place that bet.</p>');
            return;
        }

        showMessage(raceResult, '<p class="loading">Running the race…</p>');
        try {
            const result = await postJson('/api/race', { horse, bet });
            changeBalance(-bet);
            if (result.payout > 0) {
                changeBalance(result.payout);
            }
            animateRace(result.finishOrder);
            const placeList = result.finishOrder.map((horse, index) => `<li>${index + 1}. ${horse.name}</li>`).join('');
            const message = `
                <p><strong>Winner:</strong> ${result.winner}</p>
                <p><strong>Bet:</strong> $${bet}</p>
                <p><strong>${result.win ? 'You won!' : 'You lost.'}</strong></p>
                <p><strong>Payout:</strong> $${result.payout.toFixed(2)}</p>
                <p><strong>Balance:</strong> $${formatMoney(getWalletBalance())}</p>
                <div class="subcard">
                    <h3>Finish order</h3>
                    <ol>${placeList}</ol>
                </div>
            `;
            setTimeout(() => showMessage(raceResult, message), 3800);
        } catch (error) {
            showMessage(raceResult, `<p class="error">${error.message}</p>`);
        }
    });
}

if (kenoForm) {
    if (getWalletBalance() === DEFAULT_BALANCE) {
        setWalletBalance(DEFAULT_BALANCE);
    } else {
        updateWalletDisplay();
    }

    kenoForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const picksInput = document.getElementById('keno-picks').value;
        const picks = picksInput.split(',').map(n => Number(n.trim())).filter(n => !Number.isNaN(n));
        const bet = Number(document.getElementById('keno-bet').value);

        if (bet <= 0) {
            showMessage(kenoResult, '<p class="error">Enter a positive bet amount.</p>');
            return;
        }

        if (bet > getWalletBalance()) {
            showMessage(kenoResult, '<p class="error">Not enough balance to place that bet.</p>');
            return;
        }

        showMessage(kenoResult, '<p class="loading">Drawing Keno numbers…</p>');
        try {
            const result = await postJson('/api/keno', { picks, bet });
            changeBalance(-bet);
            if (result.payout > 0) {
                changeBalance(result.payout);
            }
            updateWalletDisplay();
            showMessage(kenoResult, `
                <p><strong>Your picks:</strong> ${result.picks.join(', ')}</p>
                <p><strong>Draw:</strong> ${result.draw.join(', ')}</p>
                <p><strong>Matches:</strong> ${result.matches.join(', ') || 'None'}</p>
                <p><strong>Hits:</strong> ${result.hits}</p>
                <p><strong>Payout:</strong> $${result.payout.toFixed(2)}</p>
                <p><strong>Balance:</strong> $${formatMoney(getWalletBalance())}</p>
            `);
        } catch (error) {
            showMessage(kenoResult, `<p class="error">${error.message}</p>`);
        }
    });
}

if ('serviceWorker' in navigator) {
    window.addEventListener('load', async () => {
        try {
            await navigator.serviceWorker.register('/Static/service-worker.js');
            console.log('Service worker registered.');
        } catch (error) {
            console.error('Service worker registration failed:', error);
        }
    });
}

window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
    if (installButton) {
        installButton.style.display = 'inline-flex';
    }
});

if (installButton) {
    installButton.addEventListener('click', async () => {
        if (!deferredPrompt) return;
        deferredPrompt.prompt();
        const choice = await deferredPrompt.userChoice;
        if (choice.outcome === 'accepted') {
            installButton.style.display = 'none';
        }
        deferredPrompt = null;
    });
}

updateWalletDisplay();
