// Game page JavaScript

let currentGameId = null;
let gameStarted = false;
let isPaused = false;
let displayedEventCount = 0; // Track how many events we've displayed
let socket = null;
let waitingPlayer = null; // Track which player we're waiting for (from discussion_status)
let pendingPlayers = new Set(); // Universal tracking: players with pending API calls

// Human player state
let humanPlayerName = null;
let humanRole = null;
let humanTeam = null;
let humanAlive = true;
let isRevealAll = false;
let waitingForHuman = false;
let humanInputType = null;
let humanInputContext = {};
let humanInterruptRequested = false;
let currentPhase = 'day';
let currentStep = null;
let isGameOver = false;

function initializeGame(gameId) {
    currentGameId = gameId;

    // Set up event listeners
    document.getElementById('start-btn').addEventListener('click', handleStart);
    document.getElementById('pause-btn').addEventListener('click', handlePause);

    // Human control event listeners
    document.getElementById('reveal-btn').addEventListener('click', handleToggleReveal);
    document.getElementById('interrupt-btn').addEventListener('click', handleInterrupt);
    document.getElementById('send-message').addEventListener('click', handleSendMessage);
    document.getElementById('pass-turn').addEventListener('click', handlePassTurn);
    document.getElementById('cast-vote').addEventListener('click', handleCastVote);
    document.getElementById('submit-action').addEventListener('click', handleSubmitAction);
    document.getElementById('trashtalk-interrupt-btn').addEventListener('click', handleInterrupt);
    document.getElementById('end-trashtalk-btn').addEventListener('click', handleEndTrashtalk);
    
    // Connect to WebSocket
    socket = io();
    
    // Join the game room
    socket.emit('join_game', { game_id: gameId });
    
    // Listen for game state updates
    socket.on('game_state_update', (gameState) => {
        updateDisplay(gameState);
    });

    // Listen for discussion status updates (only track waiting player)
    socket.on('discussion_status', (status) => {
        if (status.action === 'discussion_end') {
            waitingPlayer = null;
        } else {
            waitingPlayer = status.waiting_player || null;
        }
        updatePlayerIndicators();
    });

    // Listen for pause state updates
    socket.on('pause_state', (data) => {
        isPaused = data.paused;
        updatePauseButton();
    });

    // Listen for universal player status updates (pending/complete API calls)
    socket.on('player_status', (data) => {
        updatePlayerPendingStatus(data.player, data.status);
    });
    
    // Listen for connection confirmation
    socket.on('joined_game', (data) => {
        console.log('Joined game:', data.game_id);
        // Load initial state
        loadGameState();
    });
    
    // Handle connection errors
    socket.on('connect_error', (error) => {
        console.error('WebSocket connection error:', error);
        // Fallback to polling if WebSocket fails
        loadGameState();
    });
}

async function loadGameState() {
    if (!currentGameId) return;
    
    try {
        const response = await fetch(`/game/${currentGameId}/state`);
        const gameState = await response.json();
        
        if (response.ok) {
            updateDisplay(gameState);
        } else {
            console.error('Failed to load game state:', gameState.error);
        }
    } catch (error) {
        console.error('Error loading game state:', error);
    }
}

function updateDisplay(gameState) {
    // Update phase and day
    document.getElementById('phase').textContent = gameState.phase;
    document.getElementById('day').textContent = gameState.day_number;
    currentPhase = gameState.phase;
    currentStep = gameState.current_step;
    isGameOver = gameState.game_over;

    // Update human player state
    humanPlayerName = gameState.human_player_name;
    isRevealAll = gameState.reveal_all;
    waitingForHuman = gameState.waiting_for_human;
    humanInputType = gameState.human_input_type;
    humanInputContext = gameState.human_input_context || {};
    humanInterruptRequested = gameState.human_interrupt_requested;

    // Find human player's role/team/alive status
    if (humanPlayerName) {
        const humanPlayer = gameState.players.find(p => p.name === humanPlayerName);
        if (humanPlayer) {
            humanRole = humanPlayer.role;
            humanTeam = humanPlayer.team;
            humanAlive = humanPlayer.alive;
        }
    }

    // Show/hide reveal button for human games
    const revealBtn = document.getElementById('reveal-btn');
    if (humanPlayerName) {
        revealBtn.style.display = 'inline-block';
        revealBtn.textContent = isRevealAll ? 'Hide Info' : 'Reveal All';
        revealBtn.classList.toggle('active', isRevealAll);
    } else {
        revealBtn.style.display = 'none';
    }

    // Show/hide human controls container
    const humanControls = document.getElementById('human-controls');
    console.log('Human state:', {
        humanPlayerName,
        humanAlive,
        waitingForHuman,
        humanInputType,
        humanInputContext,
        gameOver: gameState.game_over,
        phase: gameState.phase
    });
    // Show controls if: human exists AND (alive OR in postgame) AND game not fully over
    const isPostgame = gameState.phase === 'postgame';
    if (humanPlayerName && (humanAlive || isPostgame) && !gameState.game_over) {
        humanControls.style.display = 'block';
        updateHumanInputUI(gameState);
    } else {
        humanControls.style.display = 'none';
    }

    // Update game status
    const statusEl = document.getElementById('game-status');
    if (gameState.game_over) {
        let winnerText = 'Town';
        if (gameState.winner === 'mafia') winnerText = 'Mafia';
        else if (gameState.winner === 'jester') winnerText = 'Jester';
        statusEl.textContent = `Game Over! ${winnerText} wins!`;
        statusEl.className = 'status game-over';
        document.getElementById('start-btn').disabled = true;
        document.getElementById('start-btn').textContent = 'Game Over';
        document.getElementById('pause-btn').disabled = true;
    } else if (gameStarted) {
        statusEl.textContent = isPaused ? 'Game paused' : 'Game running';
        statusEl.className = 'status';
    } else {
        statusEl.textContent = 'Ready to start';
        statusEl.className = 'status';
    }

    // Update players
    updatePlayers(gameState.players);

    // Update unified event log
    updateEventLog(gameState.events || [], gameState.players || []);
}

function updatePlayers(players) {
    const container = document.getElementById('players-list');
    container.innerHTML = '';

    players.forEach(player => {
        const card = document.createElement('div');
        let className = 'player-card';

        // Check if role is hidden (shown as "???")
        const isRoleHidden = player.role === '???';

        if (!player.alive) {
            className += ' dead';
        } else if (isRoleHidden) {
            className += ' unknown-role';
        } else if (player.team === 'mafia') {
            className += ' mafia';
        } else if (player.team === 'town') {
            className += ' town';
        } else if (player.team === 'third_party') {
            className += ' third_party';
        }

        // Determine if we should show waiting indicator
        // For human games: only show indicators the human should see
        const isWaitingRaw = pendingPlayers.has(player.name) || waitingPlayer === player.name;
        let shouldShowWaiting = isWaitingRaw;

        if (humanPlayerName && !isRevealAll && humanAlive && !isGameOver) {
            // Filter waiting indicators based on what human should see
            // During night: only show for actions human knows about
            if (currentPhase === 'night') {
                if (humanTeam === 'mafia') {
                    // Mafia can see mafia discussion/voting waiting
                    const isMafiaStep = currentStep?.startsWith('mafia_');
                    if (!isMafiaStep) {
                        shouldShowWaiting = false;
                    }
                } else if (humanRole === 'Doctor' || humanRole === 'Sheriff' || humanRole === 'Vigilante') {
                    // Special roles only see their own action
                    if (player.name !== humanPlayerName) {
                        shouldShowWaiting = false;
                    }
                } else {
                    // Villagers see nothing at night
                    shouldShowWaiting = false;
                }
            }
        }

        if (shouldShowWaiting) {
            className += ' waiting';
        }

        const roleDisplay = player.role || 'Unknown';
        const hasContext = player.has_context;
        const hasScratchpad = player.has_scratchpad;

        // When role is hidden, also hide context/scratchpad availability (except for own card)
        // This prevents info leaks about special roles based on scratchpad presence
        const isOwnCard = player.name === humanPlayerName;
        const canShowContextInfo = !isRoleHidden || isOwnCard;
        const showContextEnabled = canShowContextInfo && hasContext;
        const showScratchpadEnabled = canShowContextInfo && hasScratchpad;

        // Build indicator HTML
        const indicatorHtml = shouldShowWaiting ? '<span class="waiting-indicator">...</span>' : '';

        // Add "(You)" indicator for human player
        const youIndicator = isOwnCard ? '<span class="you-indicator">(You)</span>' : '';

        card.className = className;
        card.innerHTML = `
            <span class="player-name">${escapeHtml(player.name)}</span>
            ${youIndicator}
            ${indicatorHtml}
            <span class="player-role">${escapeHtml(roleDisplay)}</span>
            <span class="player-model">${escapeHtml(player.model)}</span>
            <button class="btn-context"
                    onclick="showPlayerContext('${escapeHtml(player.name).replace(/'/g, "\\'")}')"
                    ${showContextEnabled ? '' : 'disabled'}
                    title="${showContextEnabled ? 'View LLM context' : 'No context available yet'}">
                Context
            </button>
            <button class="btn-scratchpad"
                    onclick="showPlayerScratchpad('${escapeHtml(player.name).replace(/'/g, "\\'")}')"
                    ${showScratchpadEnabled ? '' : 'disabled'}
                    title="${showScratchpadEnabled ? 'View scratchpad' : 'No scratchpad notes yet'}">
                Scratchpad
            </button>
        `;

        container.appendChild(card);
    });
}

function updatePlayerIndicators() {
    // Update player cards to show/hide waiting indicators
    const cards = document.querySelectorAll('.player-card');
    cards.forEach(card => {
        const nameEl = card.querySelector('.player-name');
        if (!nameEl) return;

        const playerName = nameEl.textContent;
        const existingIndicator = card.querySelector('.waiting-indicator');

        // Check if player has pending API call or is the waitingPlayer
        const isWaiting = pendingPlayers.has(playerName) || playerName === waitingPlayer;

        // But don't show waiting indicators during night phase when there's a human player and visibility is hidden
        const shouldShowWaiting = !(humanPlayerName && currentPhase === 'night' && !isRevealAll)

        if (isWaiting && shouldShowWaiting) {
            card.classList.add('waiting');
            if (!existingIndicator) {
                const indicator = document.createElement('span');
                indicator.className = 'waiting-indicator';
                indicator.textContent = '...';
                nameEl.after(indicator);
            }
        } else {
            card.classList.remove('waiting');
            if (existingIndicator) {
                existingIndicator.remove();
            }
        }
    });
}

function updatePlayerPendingStatus(playerName, status) {
    // Update the universal pending players set
    if (status === 'pending') {
        pendingPlayers.add(playerName);
    } else if (status === 'complete') {
        pendingPlayers.delete(playerName);
    }

    // Update the UI immediately
    updatePlayerIndicators();
}

function updateEventLog(events, players) {
    const container = document.getElementById('event-log');

    // Check if user is scrolled to bottom (within 50px)
    const wasScrolledToBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 50;

    // Handle empty events
    if (events.length === 0) {
        if (container.children.length === 0 || (container.children.length === 1 && container.children[0].tagName === 'P')) {
            container.innerHTML = '<p class="empty-state">No events yet.</p>';
        }
        displayedEventCount = 0;
        return;
    }

    // If the log got shorter (shouldn't happen, but handle it), reset
    if (events.length < displayedEventCount) {
        container.innerHTML = '';
        displayedEventCount = 0;
    }

    // Remove empty placeholder if it exists
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    // Create a map of player names to their team/role
    const playerMap = {};
    players.forEach(player => {
        playerMap[player.name] = {
            team: player.team,
            role: player.role || 'Unknown'
        };
    });

    // Only append new events (incremental update)
    const newEvents = events.slice(displayedEventCount);

    newEvents.forEach(event => {
        const div = createEventElement(event, playerMap);
        if (div) {
            container.appendChild(div);
        }
    });

    // Update displayed count
    displayedEventCount = events.length;

    // Only auto-scroll if user was already at the bottom
    if (wasScrolledToBottom && newEvents.length > 0) {
        container.scrollTop = container.scrollHeight;
    }
}

function createEventElement(event, playerMap) {
    const div = document.createElement('div');
    div.className = `event-entry event-${event.type}`;
    div.dataset.eventId = event.id;
    div.dataset.visibility = event.visibility;

    // Add visibility class for private events
    if (event.visibility !== 'all' && event.visibility !== 'public') {
        div.classList.add('private-event');
        div.classList.add(`visibility-${event.visibility}`);
    }

    // Phase/day indicator
    const phaseLabel = `<span class="event-phase">${event.phase.toUpperCase()} ${event.day}</span>`;

    // Build content based on event type
    let content = '';

    if (['discussion', 'vote', 'mafia_chat', 'role_action'].includes(event.type)) {
        // Message with player name
        const playerInfo = playerMap[event.player] || {};
        const nameClass = getPlayerNameClass(playerInfo);
        const roleDisplay = playerInfo.role || 'Unknown';

        // Turn type icon for discussion messages
        let turnIcon = '';
        if (event.type === 'discussion' && event.metadata && event.metadata.turn_type) {
            if (event.metadata.turn_type === 'interrupt') {
                turnIcon = '<span class="turn-icon interrupt" title="Interrupt">⚡</span>';
            } else if (event.metadata.turn_type === 'respond') {
                turnIcon = '<span class="turn-icon respond" title="Response">↩️</span>';
            }
        }

        content = `${phaseLabel}`;
        if (event.player) {
            content += `${turnIcon}<span class="${nameClass}">${escapeHtml(event.player)} (${escapeHtml(roleDisplay)}):</span>`;
        }
        content += `<span class="event-message">${parseMarkdown(event.message)}</span>`;

        if (event.visibility !== 'all' && event.visibility !== 'public') {
            content += `<span class="visibility-badge">${getVisibilityLabel(event.visibility)}</span>`;
        }
    } else {
        // System/game event (phase_change, death, vote_result, system)
        content = `${phaseLabel}<span class="event-message">${escapeHtml(event.message)}</span>`;
    }

    div.innerHTML = content;

    return div;
}

function getPlayerNameClass(playerInfo) {
    let className = 'player-name';
    if (playerInfo.team === 'mafia') {
        className += ' mafia';
    } else if (playerInfo.team === 'town') {
        className += ' town';
    } else if (playerInfo.team === 'third_party') {
        className += ' third_party';
    }
    return className;
}

function getVisibilityLabel(visibility) {
    const labels = {
        'mafia': 'MAFIA ONLY',
        'sheriff': 'SHERIFF ONLY',
        'doctor': 'DOCTOR ONLY',
        'vigilante': 'VIGILANTE ONLY'
    };
    if (typeof visibility === 'string') {
        return labels[visibility] || visibility.toUpperCase();
    }
    return 'PRIVATE';
}

async function handleStart() {
    if (gameStarted || !currentGameId) return;

    const startBtn = document.getElementById('start-btn');
    const pauseBtn = document.getElementById('pause-btn');

    startBtn.disabled = true;
    startBtn.textContent = 'Starting...';

    try {
        const response = await fetch(`/game/${currentGameId}/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (response.ok) {
            gameStarted = true;
            startBtn.textContent = 'Running';
            pauseBtn.disabled = false;
        } else {
            console.error('Failed to start game:', data.error);
            startBtn.disabled = false;
            startBtn.textContent = 'Start Game';
        }
    } catch (error) {
        console.error('Error starting game:', error);
        startBtn.disabled = false;
        startBtn.textContent = 'Start Game';
    }
}

async function handlePause() {
    if (!currentGameId || !gameStarted) return;

    const pauseBtn = document.getElementById('pause-btn');
    pauseBtn.disabled = true;

    try {
        const response = await fetch(`/game/${currentGameId}/pause`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (response.ok) {
            isPaused = data.paused;
            updatePauseButton();
            updateStartButton();
        } else {
            console.error('Failed to toggle pause:', data.error);
        }
    } catch (error) {
        console.error('Error toggling pause:', error);
    } finally {
        pauseBtn.disabled = false;
    }
}

function updatePauseButton() {
    const pauseBtn = document.getElementById('pause-btn');

    if (isPaused) {
        pauseBtn.textContent = 'Resume';
        pauseBtn.classList.add('paused');
    } else {
        pauseBtn.textContent = 'Pause';
        pauseBtn.classList.remove('paused');
    }
}

function updateStartButton() {
    const startBtn = document.getElementById('start-btn');

    if (gameStarted) {
        if (isPaused) {
            startBtn.textContent = 'Paused';
        } else {
            startBtn.textContent = 'Running';
        }
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function parseMarkdown(text) {
    // First escape HTML to prevent XSS
    let escaped = escapeHtml(text);

    // Parse **bold** (must come before *italic* to avoid conflicts)
    escaped = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Parse *italic*
    escaped = escaped.replace(/\*(.+?)\*/g, '<em>$1</em>');

    return escaped;
}

// Context modal state
let currentContextData = null;

async function showPlayerContext(playerName) {
    if (!currentGameId) return;

    try {
        const response = await fetch(`/game/${currentGameId}/player/${encodeURIComponent(playerName)}/context`);
        const data = await response.json();

        if (!response.ok) {
            alert('Error: ' + (data.error || 'Failed to load context'));
            return;
        }

        currentContextData = data;

        // Populate modal header
        document.getElementById('modal-player-name').textContent = data.player_name;
        document.getElementById('modal-action-type').textContent = data.context.action_type || 'unknown';
        document.getElementById('modal-phase').textContent = data.context.phase || 'unknown';
        document.getElementById('modal-day').textContent = data.context.day || 'unknown';
        document.getElementById('modal-timestamp').textContent = formatTimestamp(data.context.timestamp);

        // Estimate token count (rough estimate: ~4 chars per token for English)
        const promptText = data.context.messages?.[0]?.content || '';
        const estimatedTokens = Math.ceil(promptText.length / 4);
        document.getElementById('modal-tokens').textContent = estimatedTokens.toLocaleString();

        // Set section title and display prompt
        document.getElementById('modal-section-title').textContent = 'Prompt';
        const prompt = data.context.messages?.[0]?.content || 'No prompt available';
        document.getElementById('modal-content-text').innerHTML = parseMarkdown(prompt);

        // Show modal
        document.getElementById('context-modal').classList.add('active');

    } catch (error) {
        alert('Error loading context: ' + error.message);
    }
}

function closeContextModal() {
    document.getElementById('context-modal').classList.remove('active');
    currentContextData = null;

    // Reset copy button
    const copyBtn = document.querySelector('.btn-copy');
    if (copyBtn) {
        copyBtn.textContent = 'Copy to Clipboard';
        copyBtn.classList.remove('copied');
    }
}

function copyContext() {
    if (!currentContextData) return;

    const ctx = currentContextData.context;
    const prompt = ctx.messages?.[0]?.content || 'No prompt';
    const estimatedTokens = Math.ceil(prompt.length / 4);

    const textToCopy = `=== LLM Context for ${currentContextData.player_name} ===
Action: ${ctx.action_type}
Phase: ${ctx.phase}
Day: ${ctx.day}
Timestamp: ${ctx.timestamp}
Est. Tokens: ${estimatedTokens.toLocaleString()}

=== PROMPT ===
${prompt}
`;

    navigator.clipboard.writeText(textToCopy).then(() => {
        const copyBtn = document.querySelector('.btn-copy');
        copyBtn.textContent = 'Copied!';
        copyBtn.classList.add('copied');

        setTimeout(() => {
            copyBtn.textContent = 'Copy to Clipboard';
            copyBtn.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        alert('Failed to copy: ' + err);
    });
}

// Scratchpad modal functions
async function showPlayerScratchpad(playerName) {
    if (!currentGameId) return;

    try {
        const response = await fetch(`/game/${currentGameId}/player/${encodeURIComponent(playerName)}/scratchpad`);
        const data = await response.json();

        if (!response.ok) {
            alert('Error: ' + (data.error || 'Failed to load scratchpad'));
            return;
        }

        // Populate modal
        document.getElementById('scratchpad-player-name').textContent = data.player_name;
        document.getElementById('scratchpad-day').textContent = data.note.day || 'unknown';
        document.getElementById('scratchpad-phase').textContent = data.note.phase || 'unknown';

        const timingLabels = {
            "day_start": "Day Start",
            "pre_vote": "Pre-Vote",
            "night_start": "Night Start"
        };
        document.getElementById('scratchpad-timing').textContent = timingLabels[data.note.timing] || data.note.timing;
        document.getElementById('scratchpad-timestamp').textContent = formatTimestamp(data.note.timestamp);
        document.getElementById('scratchpad-note').innerHTML = parseMarkdown(data.note.note);

        // Show modal
        document.getElementById('scratchpad-modal').classList.add('active');

    } catch (error) {
        alert('Error loading scratchpad: ' + error.message);
    }
}

function closeScratchpadModal() {
    document.getElementById('scratchpad-modal').classList.remove('active');
}

// Human Input UI Functions
function updateHumanInputUI(gameState) {
    const interruptControl = document.getElementById('interrupt-control');
    const trashtalkControl = document.getElementById('trashtalk-control');
    const discussionInput = document.getElementById('discussion-input');
    const voteInput = document.getElementById('vote-input');
    const roleInput = document.getElementById('role-input');
    const mvpVoteInput = document.getElementById('mvp-vote-input');
    const interruptStatus = document.getElementById('interrupt-status');
    const trashtalkStatus = document.getElementById('trashtalk-status');

    const isPostgame = gameState.phase === 'postgame';
    console.log('updateHumanInputUI called:', { waitingForHuman, humanInputType, humanAlive, isGameOver, isPostgame, currentStep });

    // Hide all by default
    interruptControl.style.display = 'none';
    trashtalkControl.style.display = 'none';
    discussionInput.style.display = 'none';
    voteInput.style.display = 'none';
    roleInput.style.display = 'none';
    if (mvpVoteInput) mvpVoteInput.style.display = 'none';

    // Allow input if alive OR in postgame (dead players participate in postgame)
    if ((!humanAlive && !isPostgame) || isGameOver) return;

    // Check if this is trashtalk phase (for showing end button)
    const isTrashtalk = isPostgame &&
        (currentStep === 'trashtalk_poll' || currentStep === 'trashtalk_message');

    // Check if waiting for human input
    if (waitingForHuman) {
        console.log('Showing human input UI for type:', humanInputType);
        if (humanInputType === 'discussion') {
            discussionInput.style.display = 'block';
            // Update the header with the label if provided
            const header = discussionInput.querySelector('h4');
            if (header) {
                header.textContent = humanInputContext.label || 'Your Turn to Speak';
            }
            document.getElementById('message-text').focus();
            // During trashtalk, also show the end button
            if (isTrashtalk) {
                trashtalkControl.style.display = 'block';
                document.getElementById('trashtalk-interrupt-btn').style.display = 'none';
            }
        } else if (humanInputType === 'vote') {
            voteInput.style.display = 'block';
            populateVoteOptions(humanInputContext.options || []);
        } else if (humanInputType === 'role_action') {
            roleInput.style.display = 'block';
            document.getElementById('role-action-label').textContent = humanInputContext.label || 'Choose Target';
            populateRoleOptions(humanInputContext.options || [], humanInputContext.label);
        } else if (humanInputType === 'mvp_vote') {
            if (mvpVoteInput) {
                mvpVoteInput.style.display = 'block';
                populateMvpVoteOptions(humanInputContext.options || []);
            }
        }
    } else {
        // Show interrupt button during day discussion when human is not speaking
        const isDayDiscussion = currentPhase === 'day' &&
            (currentStep === 'discussion_poll' || currentStep === 'discussion_message' ||
             currentStep?.startsWith('discussion_'));

        if (isDayDiscussion && gameState.day_number > 1) {
            interruptControl.style.display = 'block';
            if (humanInterruptRequested) {
                interruptStatus.textContent = 'Waiting for your turn...';
                document.getElementById('interrupt-btn').disabled = true;
            } else {
                interruptStatus.textContent = '';
                document.getElementById('interrupt-btn').disabled = false;
            }
        }

        // Show trashtalk controls during postgame trashtalk phase
        if (isTrashtalk) {
            trashtalkControl.style.display = 'block';
            document.getElementById('trashtalk-interrupt-btn').style.display = 'inline-block';
            if (humanInterruptRequested) {
                trashtalkStatus.textContent = 'Waiting for your turn...';
                document.getElementById('trashtalk-interrupt-btn').disabled = true;
            } else {
                trashtalkStatus.textContent = '';
                document.getElementById('trashtalk-interrupt-btn').disabled = false;
            }
        }
    }
}

function populateVoteOptions(options) {
    const select = document.getElementById('vote-target');
    select.innerHTML = '<option value="abstain">Abstain</option>';
    options.forEach(name => {
        // Can vote for anyone except yourself
        if (name !== humanPlayerName) {
            select.innerHTML += `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;
        }
    });
}

function populateRoleOptions(options, label) {
    const select = document.getElementById('role-target');
    select.innerHTML = '';

    // Add abstain option for vigilante and mafia vote
    const allowAbstain = label && (label.includes('Shoot') || label.includes('Vote to Kill'));
    if (allowAbstain) {
        select.innerHTML += '<option value="ABSTAIN">Pass / Abstain</option>';
    }

    // Allow all targets including self (players can vote to kill/shoot themselves)
    options.forEach(name => {
        select.innerHTML += `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;
    });
}

function populateMvpVoteOptions(options) {
    const select = document.getElementById('mvp-target');
    if (!select) return;
    select.innerHTML = '';
    options.forEach(name => {
        // Can't vote for yourself (already filtered by backend, but double-check)
        if (name !== humanPlayerName) {
            select.innerHTML += `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;
        }
    });
}

// Human Action Handlers
function handleToggleReveal() {
    if (!socket || !currentGameId) return;
    socket.emit('toggle_reveal', { game_id: currentGameId });
}

function handleInterrupt() {
    if (!socket || !currentGameId) return;
    socket.emit('human_interrupt', { game_id: currentGameId });
    // Disable both interrupt buttons (day and trashtalk)
    document.getElementById('interrupt-btn').disabled = true;
    document.getElementById('interrupt-status').textContent = 'Requesting turn...';
    document.getElementById('trashtalk-interrupt-btn').disabled = true;
    document.getElementById('trashtalk-status').textContent = 'Requesting turn...';
}

function handleEndTrashtalk() {
    if (!socket || !currentGameId) return;
    socket.emit('end_trashtalk', { game_id: currentGameId });
    document.getElementById('end-trashtalk-btn').disabled = true;
    document.getElementById('trashtalk-status').textContent = 'Ending trashtalk...';
}

function handleSendMessage() {
    console.log('handleSendMessage called', { socket: !!socket, currentGameId });
    if (!socket || !currentGameId) {
        console.log('Early return: no socket or gameId');
        return;
    }
    const message = document.getElementById('message-text').value.trim();
    if (!message) {
        console.log('Early return: empty message');
        return;
    }

    console.log('Emitting human_discussion:', { game_id: currentGameId, message });
    socket.emit('human_discussion', {
        game_id: currentGameId,
        message: message
    });

    document.getElementById('message-text').value = '';
    document.getElementById('discussion-input').style.display = 'none';
}

function handlePassTurn() {
    if (!socket || !currentGameId) return;

    socket.emit('human_discussion', {
        game_id: currentGameId,
        message: ''  // Empty message = pass
    });

    document.getElementById('message-text').value = '';
    document.getElementById('discussion-input').style.display = 'none';
}

function handleCastVote() {
    if (!socket || !currentGameId) return;
    const target = document.getElementById('vote-target').value;
    const explanation = document.getElementById('vote-explanation').value.trim();

    socket.emit('human_vote', {
        game_id: currentGameId,
        target: target,
        explanation: explanation
    });

    document.getElementById('vote-explanation').value = '';
    document.getElementById('vote-input').style.display = 'none';
}

function handleSubmitAction() {
    if (!socket || !currentGameId) return;
    const target = document.getElementById('role-target').value;

    socket.emit('human_role_action', {
        game_id: currentGameId,
        target: target
    });

    document.getElementById('role-input').style.display = 'none';
}

function handleMvpVote() {
    if (!socket || !currentGameId) return;
    const target = document.getElementById('mvp-target').value;
    const reason = document.getElementById('mvp-reason').value.trim() || 'Good game.';

    socket.emit('human_mvp_vote', {
        game_id: currentGameId,
        target: target,
        reason: reason
    });

    document.getElementById('mvp-reason').value = '';
    document.getElementById('mvp-vote-input').style.display = 'none';
}

function formatTimestamp(isoString) {
    if (!isoString) return 'unknown';
    try {
        const date = new Date(isoString);
        return date.toLocaleTimeString();
    } catch {
        return isoString;
    }
}

// Close modal when clicking overlay
document.addEventListener('click', (e) => {
    if (e.target.id === 'context-modal') {
        closeContextModal();
    }
});

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeContextModal();
    }
});

