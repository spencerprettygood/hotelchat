const socket = io();

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

    checkAuthStatus();
    fetchConversations();
    setInterval(fetchConversations, 3000); // Poll every 3 seconds
});

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
                loginPage.style.display = 'none';
                dashboardSection.style.display = 'block';
                fetchConversations();
            } else {
                loginPage.style.display = 'flex';
                dashboardSection.style.display = 'none';
            }
        })
        .catch(error => {
            console.error('Error checking auth status:', error);
            alert('Failed to check authentication status. Please try again.');
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
                document.getElementById('loginPage').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
                fetchConversations();
            } else {
                alert('Login failed: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error during login:', error);
            alert('Login failed. Please try again.');
        });
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
            .catch(error => {
                console.error('Error during logout:', error);
                alert('Logout failed. Please try again.');
            });
    });
} else {
    console.error('Logout button not found.');
}

let fetchTimeout;
function fetchConversations() {
    clearTimeout(fetchTimeout);
    fetchTimeout = setTimeout(async () => {
        const conversationList = document.getElementById('conversationList');
        if (!conversationList) {
            console.error('Conversation list (conversationList) is missing.');
            return;
        }

        try {
            const response = await fetch(`/conversations?filter=${currentFilter}`);
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            const conversations = await response.json();

            conversationList.innerHTML = '';

            let filteredConversations = conversations.filter(convo => {
                if (currentChannel && convo.channel !== currentChannel) {
                    return false;
                }
                return true;
            });

            updateCounts(conversations);

            filteredConversations.forEach(convo => {
                const li = document.createElement('li');
                li.className = 'conversation-item';

                const convoContainer = document.createElement('div');
                convoContainer.style.display = 'flex';
                convoContainer.style.justifyContent = 'space-between';
                convoContainer.style.alignItems = 'center';

                const convoInfo = document.createElement('span');
                convoInfo.textContent = `${convo.username} (${convo.channel}): Assigned to ${convo.assigned_agent || 'unassigned'}`;
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
        } catch (error) {
            console.error('Error fetching conversations:', error);
            alert('Failed to fetch conversations. Please try again.');
        }
    }, 500); // Debounce for 500ms
}

// Update conversation counts
function updateCounts(conversations) {
    const unassignedCount = document.getElementById('unassignedCount');
    const yourCount = document.getElementById('yourCount');
    const teamCount = document.getElementById('teamCount');
    const allCount = document.getElementById('allCount');

    if (unassignedCount && yourCount && teamCount && allCount) {
        unassignedCount.textContent = conversations.filter(c => !c.assigned_agent).length;
        yourCount.textContent = conversations.filter(c => c.assigned_agent === currentAgent).length;
        teamCount.textContent = conversations.filter(c => c.assigned_agent && c.assigned_agent !== currentAgent).length;
        allCount.textContent = conversations.length;
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

// Take over a conversation
function takeOverConversation(convoId) {
    fetch('/handoff', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            conversation_id: convoId,
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message) {
                fetchConversations();
                if (currentConversationId === convoId) {
                    loadConversation(convoId);
                }
            } else {
                alert('Error assigning chat: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error during handoff:', error);
            alert('Failed to take over the conversation. Please try again.');
        });
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
                div.className = 'message';
                div.classList.add(isUser ? 'user-message' : 'agent-message');

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
        })
        .catch(error => {
            console.error('Error loading messages:', error);
            alert('Failed to load conversation messages. Please try again.');
        });
}

// Send a message
function sendMessage() {
    if (!currentConversationId) {
        alert('Please select a conversation.');
        return;
    }

    const messageInput = document.getElementById('messageInput');
    if (!messageInput) {
        console.error('Message input not found.');
        return;
    }

    const message = messageInput.value.trim();
    if (!message) return;

    fetch('/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            conversation_id: currentConversationId,
            message: message,
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.reply) {
                // AI response will be handled via Socket.IO
            } else if (data.status === 'success') {
                messageInput.value = '';
                loadConversation(currentConversationId); // Refresh the chat
            } else {
                console.error('Error sending message:', data.error);
                alert('Failed to send message: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error sending message:', error);
            alert('Failed to send message. Please try again.');
        });
}

// Hand back to AI
function handBackToAI(convoId) {
    fetch('/handback-to-ai', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ conversation_id: convoId }),
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
                fetchConversations();
                if (currentConversationId === convoId) {
                    loadConversation(convoId);
                }
            } else {
                alert('Failed to hand back to AI: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error handing back to AI:', error);
            alert('Failed to hand back to AI. Please try again.');
        });
}

// Socket.IO event listeners
socket.on('new_message', (data) => {
    console.log('New message received:', data);
    if (data.convo_id === currentConversationId) {
        const chatBox = document.getElementById('chatBox');
        if (chatBox) {
            const div = document.createElement('div');
            const isUser = data.sender === 'user';
            div.className = 'message';
            div.classList.add(isUser ? 'user-message' : 'agent-message');

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
});

socket.on('error', (data) => {
    console.error('Error received:', data);
    alert(data.message);
});

socket.on('refresh_conversations', (data) => {
    console.log('Refresh conversations event received:', data);
    fetchConversations();
});

socket.on('notify_agent', (data) => {
    if (data.agent === currentAgent) {
        const notificationArea = document.getElementById('notificationArea');
        if (notificationArea) {
            notificationArea.textContent = `New conversation assigned to you: ${data.conversation_id}`;
            notificationArea.style.display = 'block';
            setTimeout(() => {
                notificationArea.style.display = 'none';
            }, 5000);
        }
        fetchConversations();
    }
});
