let currentConversationId = null;
let currentAgent = null;

// Check authentication status on page load
document.addEventListener('DOMContentLoaded', () => {
    const loginPage = document.getElementById('loginPage');
    const dashboardSection = document.getElementById('dashboard');
    if (!loginPage || !dashboardSection) {
        console.error('Required DOM elements (loginPage, dashboard) are missing.');
        return;
    }

    checkAuthStatus();
    fetchAISetting();
    fetchConversations();

    const messageInput = document.getElementById('message-input');
    if (messageInput) {
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' || e.keyCode === 13) {
                e.preventDefault();
                sendMessage();
            }
        });
    } else {
        console.error('Message input not found for adding Enter key listener.');
    }
});

// Fetch AI setting on page load
function fetchAISetting() {
    fetch('/settings')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            const aiToggle = document.getElementById('ai-toggle');
            const aiToggleError = document.getElementById('ai-toggle-error');
            if (aiToggle && aiToggleError) {
                aiToggle.checked = data.ai_enabled === '1';
                aiToggleError.style.display = 'none';
                aiToggle.addEventListener('change', () => {
                    toggleAI(aiToggle.checked);
                });
            } else {
                console.error('AI toggle or error element not found on live-messages page.');
            }
        })
        .catch(error => {
            console.error('Error fetching AI setting:', error);
            const aiToggleError = document.getElementById('ai-toggle-error');
            if (aiToggleError) {
                aiToggleError.textContent = 'Failed to load AI setting: ' + error.message;
                aiToggleError.style.display = 'inline';
            }
        });
}

// Toggle AI setting
function toggleAI(enabled) {
    const aiToggleError = document.getElementById('ai-toggle-error');
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ key: 'ai_enabled', value: enabled ? '1' : '0' }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                console.log('AI setting updated successfully');
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

// Check if user is authenticated
function checkAuthStatus() {
    fetch('/check-auth')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            const loginPage = document.getElementById('loginPage');
            const dashboardSection = document.getElementById('dashboard');
            if (data.is_authenticated) {
                currentAgent = data.agent;
                console.log("Current Agent:", currentAgent);
                loginPage.style.display = 'none';
                dashboardSection.style.display = 'flex';
                fetchConversations();
            } else {
                loginPage.style.display = 'flex';
                dashboardSection.style.display = 'none';
            }
        })
        .catch(error => console.error('Error checking auth status:', error));
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
                document.getElementById('dashboard').style.display = 'flex';
                fetchConversations();
            } else {
                alert('Login failed: ' + data.message);
            }
        })
        .catch(error => console.error('Error during login:', error));
}

// Logout button
const logoutButton = document.getElementById('logout-button');
if (logoutButton) {
    logoutButton.addEventListener('click', () => {
        fetch('/logout', {
            method: 'POST',
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

function fetchConversations() {
    const conversationList = document.getElementById('conversation-list');
    const convoLoadingSpinner = document.getElementById('convo-loading-spinner');
    if (!conversationList) {
        console.error('Conversation list (conversation-list) is missing.');
        return;
    }

    if (convoLoadingSpinner) convoLoadingSpinner.style.display = 'block';
    fetch('/all-whatsapp-messages')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            conversationList.innerHTML = '';
            if (data.conversations) {
                data.conversations.forEach(convo => {
                    const convoItem = document.createElement('div');
                    convoItem.classList.add('conversation-item');
                    if (currentConversationId === convo.convo_id) {
                        convoItem.classList.add('active');
                    }
                    convoItem.innerHTML = `
                        <div class="avatar"></div>
                        <div class="info">
                            <div class="name">${convo.username}</div>
                            <div class="last-message">${convo.messages.length > 0 ? convo.messages[convo.messages.length - 1].message : 'No messages'}</div>
                        </div>
                    `;
                    convoItem.addEventListener('click', () => loadConversation(convo.convo_id));
                    conversationList.appendChild(convoItem);
                });
            }
        })
        .catch(error => {
            console.error('Error fetching conversations:', error);
        })
        .finally(() => {
            if (convoLoadingSpinner) convoLoadingSpinner.style.display = 'none';
        });
}

// Load a conversation into the active panel
function loadConversation(convoId) {
    currentConversationId = convoId;
    const chatBox = document.getElementById('chat-box');
    const clientName = document.getElementById('chat-title');
    const chatHeader = document.getElementById('chat-header');
    const inputContainer = document.getElementById('input-container');
    if (!chatBox || !clientName) {
        console.error('Chat box or client name element not found.');
        return;
    }

    if (chatHeader) chatHeader.style.display = 'flex';
    if (inputContainer) inputContainer.style.display = 'flex';

    fetch(`/messages?conversation_id=${convoId}`)
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
            });

            chatBox.scrollTop = chatBox.scrollHeight;
            clientName.textContent = username;

            // Update active conversation item
            const conversationItems = document.querySelectorAll('.conversation-item');
            conversationItems.forEach(item => item.classList.remove('active'));
            const selectedItem = Array.from(conversationItems).find(item => item.textContent.includes(username));
            if (selectedItem) selectedItem.classList.add('active');
        })
        .catch(error => console.error('Error loading messages:', error));
}

function sendMessage() {
    if (!currentConversationId) {
        alert('Please select a conversation.');
        return;
    }

    const messageInput = document.getElementById('message-input');
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

    fetch('/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            convo_id: currentConversationId,
            message: message,
            channel: 'whatsapp'
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Failed to send message: ${response.status} ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                messageInput.value = '';
                // Message will be appended via Socket.IO 'new_message' event
            } else if (data.reply) {
                console.log('AI response will be handled via Socket.IO:', data.reply);
            } else {
                console.error('Error sending message:', data.error);
                alert('Failed to send message: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error sending message:', error);
            alert('Error sending message: ' + error.message);
        });
}

// Socket.IO event listeners
socket.on('new_message', (data) => {
    console.log('New message received:', data);
    if (data.convo_id === currentConversationId) {
        const chatBox = document.getElementById('chat-box');
        if (chatBox) {
            const div = document.createElement('div');
            const isUser = data.sender === 'user';
            const isAgent = data.sender === 'agent';
            div.className = 'message';
            div.classList.add(isUser ? 'user-message' : isAgent ? 'agent-message' : 'ai-message');

            const textSpan = document.createElement('span');
            textSpan.textContent = data.message;
            div.appendChild(textSpan);

            const timestampSpan = document.createElement('span');
            timestampSpan.className = 'message-timestamp';
            const now = new Date();
            timestampSpan.textContent = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
            div.appendChild(timestampSpan);

            chatBox.appendChild(div);
            chatBox.scrollTop = chatBox.scrollHeight;
        }
    }
    fetchConversations();
    const conversationList = document.getElementById('conversation-list');
    const convoItem = Array.from(conversationList.children).find(item => item.textContent.includes(data.username));
    if (convoItem) {
        convoItem.querySelector('.last-message').textContent = data.message;
    }
});

socket.on('error', (data) => {
    console.error('Error received:', data);
    alert(data.message);
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
    if (currentConversationId) {
        loadConversation(currentConversationId);
    }
});

socket.on("reconnect_error", (error) => {
    console.error("Reconnection failed:", error);
});
