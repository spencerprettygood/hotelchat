const socket = io({
    transports: ["websocket", "polling"],
});

let currentConversationId = null;
let currentFilter = 'unassigned';
let currentChannel = null;
let currentAgent = null;

// Check authentication status on page load
document.addEventListener('DOMContentLoaded', () => {
    const loginPage = document.getElementById('loginPage');
    const dashboardSection = document.getElementById('dashboard');
    if (!loginPage || !dashboardSection) {
        console.error('Required DOM elements (loginPage, dashboard) are missing.');
        return;
    }

    // Check authentication status before fetching data
    checkAuthStatus().then(isAuthenticated => {
        if (isAuthenticated) {
            fetchAISetting();
            fetchConversations();
            setInterval(fetchConversations, 10000);
        }
    });

    const messageInput = document.getElementById('messageInput');
    if (messageInput) {
        // Add keypress listener for sending messages
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' || e.keyCode === 13) {
                e.preventDefault();
                sendMessage();
            }
        });
    } else {
        console.error('Message input not found for adding Enter key listener.');
    }

    // Add event listener for the Live Messages button
    const liveMessagesButton = document.getElementById('liveMessagesButton');
    if (liveMessagesButton) {
        liveMessagesButton.addEventListener('click', () => {
            checkAuthStatus().then(isAuthenticated => {
                if (isAuthenticated) {
                    window.location.href = '/live-messages/';
                } else {
                    alert('You must be logged in to access the Live Messages page.');
                    window.location.href = '/login';
                }
            });
        });
    } else {
        console.error('Live Messages button not found.');
    }

    // Auto-logout after 1 hour of inactivity
    const INACTIVITY_TIMEOUT = 60 * 60 * 1000; // 1 hour in milliseconds
    let inactivityTimer;

    function resetInactivityTimer() {
        console.log("Resetting inactivity timer");
        clearTimeout(inactivityTimer);
        inactivityTimer = setTimeout(() => {
            console.log("Inactivity timeout reached, logging out");
            fetch("/logout", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: 'include'
            })
                .then((response) => response.json())
                .then((data) => {
                    if (data.message === "Logged out successfully") {
                        alert("Logged out due to inactivity");
                        window.location.href = "/login";
                    } else {
                        alert("Auto-logout failed: " + (data.message || "Unknown error"));
                    }
                })
                .catch((error) => {
                    console.error("Error during auto-logout:", error);
                    alert("An error occurred during auto-logout: " + error.message);
                });
        }, INACTIVITY_TIMEOUT);
    }

    // Track user activity
    ["mousemove", "mousedown", "keypress", "scroll", "touchstart"].forEach((event) => {
        document.addEventListener(event, resetInactivityTimer, { passive: true });
    });

    // Start the timer on page load
    resetInactivityTimer();

    // Add channel filter UI (e.g., a dropdown)
    const channelFilter = document.createElement('select');
    channelFilter.id = 'channelFilter';
    channelFilter.className = 'p-2 border rounded-lg';
    channelFilter.innerHTML = `
        <option value="">All Channels</option>
        <option value="whatsapp">WhatsApp</option>
        <option value="instagram">Instagram</option>
    `;
    channelFilter.addEventListener('change', (e) => {
        filterByChannel(e.target.value);
    });
    const filterSection = document.getElementById('filter-section');
    if (filterSection) {
        filterSection.appendChild(channelFilter);
    } else {
        console.error('Filter section not found for adding channel filter.');
    }
});

// Check if user is authenticated
async function checkAuthStatus() {
    try {
        const response = await fetch('/check-auth', {
            method: 'GET',
            credentials: 'include'
        });
        if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
        }
        const data = await response.json();
        const loginPage = document.getElementById('loginPage');
        const dashboardSection = document.getElementById('dashboard');
        if (data.is_authenticated) {
            currentAgent = data.agent;
            console.log("Current Agent:", currentAgent);
            loginPage.style.display = 'none';
            dashboardSection.style.display = 'block';
            return true;
        } else {
            loginPage.style.display = 'flex';
            dashboardSection.style.display = 'none';
            return false;
        }
    } catch (error) {
        console.error('Error checking auth status:', error);
        const loginPage = document.getElementById('loginPage');
        const dashboardSection = document.getElementById('dashboard');
        loginPage.style.display = 'flex';
        dashboardSection.style.display = 'none';
        return false;
    }
}

// Fetch AI setting on page load
async function fetchAISetting() {
    const isAuthenticated = await checkAuthStatus();
    if (!isAuthenticated) {
        console.log('User not authenticated, skipping fetchAISetting');
        return;
    }

    try {
        const response = await fetch('/settings', {
            method: 'GET',
            credentials: 'include'
        });
        if (!response.ok) {
            if (response.status === 401) {
                console.log('Unauthorized, redirecting to login');
                window.location.href = '/login';
                return;
            }
            throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
        }
        const data = await response.json();
        const aiToggle = document.getElementById('ai-toggle');
        const aiToggleError = document.getElementById('ai-toggle-error');
        if (aiToggle && aiToggleError) {
            aiToggle.checked = data.ai_enabled === '1';
            aiToggleError.style.display = 'none';
            aiToggle.addEventListener('change', () => {
                toggleAI(aiToggle.checked);
            });
        } else {
            console.error('AI toggle or error element not found on dashboard page.');
        }
    } catch (error) {
        console.error('Error fetching AI setting:', error);
        const aiToggleError = document.getElementById('ai-toggle-error');
        if (aiToggleError) {
            aiToggleError.textContent = 'Failed to load AI setting: ' + error.message;
            aiToggleError.style.display = 'inline';
        }
    }
}

// Toggle global AI setting
function toggleAI(enabled) {
    const aiToggleError = document.getElementById('ai-toggle-error');
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ key: 'ai_enabled', value: enabled ? '1' : '0' }),
        credentials: 'include'
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                console.log('Global AI setting updated successfully');
                if (aiToggleError) {
                    aiToggleError.style.display = 'none';
                }
            } else {
                throw new Error(data.error || 'Failed to update AI settings');
            }
        })
        .catch(error => {
            console.error('Error updating AI settings:', error);
            if (aiToggleError) {
                aiToggleError.textContent = 'Error updating AI settings: ' + error.message;
                aiToggleError.style.display = 'inline';
            }
            const aiToggle = document.getElementById('ai-toggle');
            if (aiToggle) {
                aiToggle.checked = !enabled; // Revert on error
            }
        });
}

// Login function for the button
function login() {
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    if (!usernameInput || !passwordInput) {
        console.error('Username or password input missing.');
        return;
    }

    const username = usernameInput.value;
    const password = passwordInput.value;

    fetch('/login', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
        credentials: 'include'
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message === 'Login successful') {
                currentAgent = data.agent;
                console.log("Current Agent:", currentAgent);
                document.getElementById('loginPage').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
                fetchConversations();
            } else {
                alert('Login failed: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error during login:', error);
            alert('Error during login: ' + error.message);
        });
}

// Logout button
const logoutButton = document.getElementById('logout-button');
if (logoutButton) {
    logoutButton.addEventListener('click', () => {
        fetch('/logout', {
            method: 'POST',
            credentials: 'include'
        })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.message === 'Logged out successfully') {
                    currentAgent = null;
                    document.getElementById('loginPage').style.display = 'flex';
                    document.getElementById('dashboard').style.display = 'none';
                }
            })
            .catch(error => console.error('Error during logout:', error));
    });
} else {
    console.error('Logout button not found.');
}

async function fetchConversations() {
    const isAuthenticated = await checkAuthStatus();
    if (!isAuthenticated) {
        console.log('User not authenticated, skipping fetchConversations');
        return;
    }

    const conversationList = document.getElementById('conversationList');
    const conversationError = document.getElementById('conversation-error');
    if (!conversationList) {
        console.error('Conversation list (conversationList) is missing.');
        return;
    }
    if (!conversationError) {
        console.error('Conversation error element (conversation-error) is missing.');
    }

    try {
        const response = await fetch('/conversations', {
            method: 'GET',
            credentials: 'include'
        });
        if (!response.ok) {
            if (response.status === 401) {
                console.log('Unauthorized, redirecting to login');
                window.location.href = '/login';
                return;
            }
            throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
        }
        const conversations = await response.json();
        console.log('Fetched Conversations:', conversations);
        conversationList.innerHTML = '';
        if (conversationError) {
            conversationError.style.display = 'none';
        }

        console.log('Current Filter:', currentFilter, 'Current Channel:', currentChannel, 'Current Agent:', currentAgent);
        let filteredConversations = conversations.filter(convo => {
            // Backend already filters by needs_agent = 1, but we keep this check for safety
            if (!convo.needs_agent) {
                return false;
            }
            if (currentChannel && convo.channel !== currentChannel) {
                return false;
            }
            if (currentFilter === 'unassigned') {
                return !convo.assigned_agent;
            } else if (currentFilter === 'you') {
                return convo.assigned_agent === currentAgent;
            } else if (currentFilter === 'team') {
                return convo.assigned_agent && convo.assigned_agent !== currentAgent;
            }
            return true;
        });

        console.log('Filtered Conversations:', filteredConversations);

        updateCounts(conversations);

        filteredConversations.forEach(convo => {
            console.log("Filter Check:", currentFilter, convo.username, convo.assigned_agent, currentAgent);
            const li = document.createElement('li');
            li.className = 'conversation-item';

            const convoContainer = document.createElement('div');
            convoContainer.style.display = 'flex';
            convoContainer.style.justifyContent = 'space-between';
            convoContainer.style.alignItems = 'center';

            const convoInfo = document.createElement('span');
            // Display AI enabled status in the UI
            const aiStatus = convo.ai_enabled ? '(AI Enabled)' : '(AI Disabled)';
            convoInfo.textContent = `${convo.username} (${convo.channel}): Assigned to ${convo.assigned_agent || 'unassigned'} ${aiStatus}`;
            convoInfo.onclick = () => loadConversation(convo.id);
            convoInfo.style.cursor = 'pointer';
            convoContainer.appendChild(convoInfo);

            if (currentFilter === 'unassigned' && !convo.assigned_agent) {
                const takeOverButton = document.createElement('button');
                takeOverButton.textContent = 'Take Over';
                takeOverButton.onclick = (e) => {
                    e.stopPropagation();
                    takeOverConversation(convo.id);
                };
                takeOverButton.className = 'take-over-btn';
                convoContainer.appendChild(takeOverButton);
            }

            if (currentFilter === 'you' && convo.assigned_agent === currentAgent) {
                const handBackButton = document.createElement('button');
                handBackButton.textContent = 'Hand Back to AI';
                handBackButton.onclick = (e) => {
                    e.stopPropagation();
                    handBackToAI(convo.id);
                };
                handBackButton.className = 'handback-button';
                convoContainer.appendChild(handBackButton);
            }

            li.appendChild(convoContainer);
            conversationList.appendChild(li);
        });

        // If the currently selected conversation is no longer visible, clear the chat box
        if (currentConversationId && !filteredConversations.some(convo => convo.id === currentConversationId)) {
            currentConversationId = null;
            const chatBox = document.getElementById('chatBox');
            const clientName = document.getElementById('clientName');
            if (chatBox && clientName) {
                chatBox.innerHTML = '';
                clientName.textContent = 'Select a conversation';
                updateChatControls(null); // Disable chat controls
            }
        } else if (currentConversationId) {
            // Update chat controls for the current conversation
            updateChatControls(currentConversationId);
        }
    } catch (error) {
        console.error('Error fetching conversations:', error);
        if (conversationError) {
            conversationError.textContent = 'Failed to load conversations: ' + error.message;
            conversationError.style.display = 'inline';
        }
    }
}

// Update conversation counts
function updateCounts(conversations) {
    const unassignedCount = document.getElementById('unassignedCount');
    const yourCount = document.getElementById('yourCount');
    const teamCount = document.getElementById('teamCount');
    const allCount = document.getElementById('allCount');

    if (unassignedCount && yourCount && teamCount && allCount) {
        const visibleConversations = conversations.filter(c => c.needs_agent); // Only count visible conversations
        unassignedCount.textContent = visibleConversations.filter(c => !c.assigned_agent).length;
        yourCount.textContent = visibleConversations.filter(c => c.assigned_agent === currentAgent).length;
        teamCount.textContent = visibleConversations.filter(c => c.assigned_agent && c.assigned_agent !== currentAgent).length;
        allCount.textContent = visibleConversations.length;
    }
}

// Load conversations based on filter
function loadConversations(filter) {
    currentFilter = filter;
    fetchConversations();
}

// Filter by channel
function filterByChannel(channel) {
    currentChannel = channel;
    fetchConversations();
}

// Update chat controls based on conversation state
async function updateChatControls(convoId) {
    const messageInput = document.getElementById('messageInput');
    const sendButton = document.querySelector('button[onclick="sendMessage()"]');
    if (!messageInput || !sendButton) {
        console.error('Message input or send button not found.');
        return;
    }

    if (!convoId) {
        // No conversation selected, disable chat controls
        messageInput.disabled = true;
        sendButton.disabled = true;
        return;
    }

    try {
        const response = await fetch('/conversations', {
            method: 'GET',
            credentials: 'include'
        });
        if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
        }
        const conversations = await response.json();
        const convo = conversations.find(c => c.id === parseInt(convoId));
        if (convo && convo.assigned_agent === currentAgent) {
            // Agent has taken over, enable chat controls
            messageInput.disabled = false;
            sendButton.disabled = false;
        } else {
            // Agent has not taken over, disable chat controls
            messageInput.disabled = true;
            sendButton.disabled = true;
        }
    } catch (error) {
        console.error('Error updating chat controls:', error);
        messageInput.disabled = true;
        sendButton.disabled = true;
    }
}

// Take over a conversation
function takeOverConversation(convoId) {
    fetch('/handoff', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            conversation_id: convoId,
            disable_ai: true, // Indicate that AI should be disabled for this conversation
        }),
        credentials: 'include'
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message) {
                currentFilter = 'you';
                fetchConversations();
                if (currentConversationId === convoId) {
                    loadConversation(convoId);
                    updateChatControls(convoId); // Enable chat controls after taking over
                }
            } else {
                alert('Error assigning chat: ' + data.error);
            }
        })
        .catch(error => console.error('Error during handoff:', error));
}

// Load a conversation into the active panel
function loadConversation(convoId) {
    currentConversationId = convoId;
    const chatBox = document.getElementById('chatBox');
    const clientName = document.getElementById('clientName');
    if (!chatBox || !clientName) {
        console.error('Chat box or client name element not found.');
        return;
    }

    // Join the Socket.IO room for this conversation
    socket.emit('join_conversation', { convo_id: convoId });

    fetch(`/messages/${convoId}`, {
        credentials: 'include'
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            const messages = data.messages;
            const username = data.username;
            chatBox.innerHTML = '';

            messages.forEach(msg => {
                appendMessage(msg);
            });

            chatBox.scrollTop = chatBox.scrollHeight;
            clientName.textContent = username;

            // Update chat controls based on conversation state
            updateChatControls(convoId);
        })
        .catch(error => console.error('Error loading messages:', error));
}

// Helper function to append a message to the chat box
function appendMessage(msg) {
    const chatBox = document.getElementById('chatBox');
    if (!chatBox) return;

    const div = document.createElement('div');
    const isUser = msg.sender === 'user';
    const isAgent = msg.sender === 'agent';
    div.className = 'message';
    div.classList.add(isUser ? 'user-message' : isAgent ? 'agent-message' : 'ai-message');
    const textSpan = document.createElement('span');
    textSpan.textContent = msg.message;
    div.appendChild(textSpan);

    const timestampSpan = document.createElement('span');
    timestampSpan.className = 'message-timestamp';
    const timeMatch = msg.timestamp.match(/\d{2}:\d{2}/);
    timestampSpan.textContent = timeMatch ? timeMatch[0] : msg.timestamp;
    div.appendChild(timestampSpan);

    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function sendMessage() {
    if (!currentConversationId) {
        alert('Please select a conversation.');
        return;
    }

    const messageInput = document.getElementById('messageInput');
    if (!messageInput) {
        console.error('Message input not found.');
        alert('Error: Message input field not found.');
        return;
    }

    const message = messageInput.value.trim();
    if (!message) {
        alert('Please enter a message to send.');
        return;
    }

    // Optimistically add the message to the UI
    const optimisticMessage = {
        message: message,
        sender: 'agent',
        timestamp: new Date().toISOString()
    };
    appendMessage(optimisticMessage);

    // Use Socket.IO to send the message
    socket.emit('agent_message', {
        convo_id: currentConversationId,
        message: message,
        channel: 'whatsapp',
    }, (response) => {
        if (response && response.error) {
            console.error('Error sending message:', response.error);
            alert('Failed to send message: ' + response.error);
            // Remove the optimistic message if the send fails
            loadConversation(currentConversationId);
        }
    });

    messageInput.value = ''; // Clear input after sending
}

// Hand back to AI
function handBackToAI(convoId) {
    fetch('/handback-to-ai', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
            conversation_id: convoId,
            enable_ai: true, // Indicate that AI should be re-enabled for this conversation
            clear_needs_agent: true // Indicate that this conversation no longer needs agent intervention
        }),
        credentials: 'include'
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message) {
                alert(data.message);
                socket.emit('refresh_conversations', { conversation_id: convoId });
                if (currentConversationId === convoId) {
                    // Leave the Socket.IO room for this conversation
                    socket.emit('leave_conversation', { convo_id: convoId });
                    currentConversationId = null;
                    const chatBox = document.getElementById('chatBox');
                    const clientName = document.getElementById('clientName');
                    if (chatBox && clientName) {
                        chatBox.innerHTML = '';
                        clientName.textContent = 'Select a conversation';
                        updateChatControls(null); // Disable chat controls
                    }
                }
                fetchConversations();
            } else {
                alert('Failed to hand back to AI: ' + data.error);
            }
        })
        .catch(error => console.error('Error handing back to AI:', error));
}

// Socket.IO event listeners
socket.on('new_message', (data) => {
    console.log('New message received:', data);
    if (data.convo_id === currentConversationId) {
        appendMessage({
            message: data.message,
            sender: data.sender,
            timestamp: data.timestamp
        });
    }
    fetchConversations();
});

socket.on('error', (data) => {
    console.error('Error received:', data);
    alert(data.message);
});

socket.on('refresh_conversations', (data) => {
    console.log('Refresh conversations event received:', data);
    const conversationId = data.conversation_id;
    pollVisibility(conversationId);
    fetchConversations();
});

socket.on('settings_updated', (data) => {
    const aiToggle = document.getElementById('ai-toggle');
    const aiToggleError = document.getElementById('ai-toggle-error');
    if (aiToggle && aiToggleError) {
        aiToggle.checked = data.ai_enabled === '1';
        aiToggleError.style.display = 'none';
    }
});

socket.on("reconnect", (attempt) => {
    console.log(`Reconnected to Socket.IO after ${attempt} attempts`);
    fetchConversations();
});

socket.on("reconnect_error", (error) => {
    console.error("Reconnection failed:", error);
});

// Poll visibility of a conversation
function pollVisibility(conversationId) {
    fetch(`/check-visibility?conversation_id=${conversationId}`, {
        credentials: 'include'
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.visible) {
                console.log(`Conversation ${conversationId} is now visible`);
                fetchConversations();
            } else {
                console.log(`Conversation ${conversationId} is not yet visible, polling again...`);
                setTimeout(() => pollVisibility(conversationId), 1000);
            }
        })
        .catch(error => {
            console.error('Error polling visibility:', error);
            setTimeout(() => pollVisibility(conversationId), 1000);
        });
}
