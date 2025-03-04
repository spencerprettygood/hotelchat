// dashboard.js
const socket = io();

let currentConversationId = null;

// Check authentication status on page load
document.addEventListener('DOMContentLoaded', () => {
    checkAuthStatus();
    fetchConversations();
    setInterval(fetchConversations, 5000); // Poll every 5 seconds
});

// Check if user is authenticated
function checkAuthStatus() {
    fetch('/check-auth')
        .then(response => response.json())
        .then(data => {
            if (data.is_authenticated) {
                document.getElementById('login-section').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
            } else {
                document.getElementById('login-section').style.display = 'block';
                document.getElementById('dashboard').style.display = 'none';
            }
        })
        .catch(error => console.error('Error checking auth status:', error));
}

// Login form submission
document.getElementById('login-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;

    fetch('/login', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
    })
        .then(response => response.json())
        .then(data => {
            if (data.message === 'Login successful') {
                document.getElementById('login-section').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
                fetchConversations();
            } else {
                alert('Login failed: ' + data.message);
            }
        })
        .catch(error => console.error('Error during login:', error));
});

// Logout button
document.getElementById('logout-button').addEventListener('click', () => {
    fetch('/logout', {
        method: 'POST',
    })
        .then(response => response.json())
        .then(data => {
            if (data.message === 'Logged out successfully') {
                document.getElementById('login-section').style.display = 'block';
                document.getElementById('dashboard').style.display = 'none';
            }
        })
        .catch(error => console.error('Error during logout:', error));
});

// Fetch conversations
function fetchConversations() {
    fetch('/conversations')
        .then(response => response.json())
        .then(conversations => {
            const unassignedList = document.getElementById('unassigned-conversations');
            const yourList = document.getElementById('your-conversations');
            unassignedList.innerHTML = '';
            yourList.innerHTML = '';

            conversations.forEach(convo => {
                const li = document.createElement('li');
                li.textContent = `${convo.username} (${convo.channel}): ${convo.latest_message}`;
                li.dataset.convoId = convo.id;
                li.onclick = () => loadConversation(convo.id);

                if (convo.assigned_agent) {
                    yourList.appendChild(li);
                } else {
                    unassignedList.appendChild(li);
                }
            });
        })
        .catch(error => console.error('Error fetching conversations:', error));
}

// Load a conversation into the active panel
function loadConversation(convoId) {
    currentConversationId = convoId;
    fetch(`/messages?conversation_id=${convoId}`)
        .then(response => response.json())
        .then(messages => {
            const chatWindow = document.getElementById('chat-window');
            chatWindow.innerHTML = '';

            messages.forEach(msg => {
                const div = document.createElement('div');
                div.className = msg.sender === 'user' ? 'user-message' : 'ai-message';
                div.textContent = `${msg.sender}: ${msg.message} (${msg.timestamp})`;
                chatWindow.appendChild(div);
            });

            chatWindow.scrollTop = chatWindow.scrollHeight;

            // Show handoff button if unassigned
            fetch(`/conversations`)
                .then(response => response.json())
                .then(conversations => {
                    const convo = conversations.find(c => c.id === convoId);
                    if (convo && !convo.assigned_agent) {
                        document.getElementById('handoff-button').style.display = 'block';
                    } else {
                        document.getElementById('handoff-button').style.display = 'none';
                    }
                });
        })
        .catch(error => console.error('Error loading messages:', error));
}

// Send a message
document.getElementById('send-message').addEventListener('click', () => {
    if (!currentConversationId) {
        alert('Please select a conversation.');
        return;
    }

    const messageInput = document.getElementById('message-input');
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
        .then(response => response.json())
        .then(data => {
            if (data.reply) {
                // If AI responds, it will be handled via Socket.IO
            } else if (data.status === 'success') {
                // Agent message sent successfully
                messageInput.value = '';
            } else {
                console.error('Error sending message:', data.error);
            }
        })
        .catch(error => console.error('Error sending message:', error));
});

// Handoff button
document.getElementById('handoff-button').addEventListener('click', () => {
    if (!currentConversationId) {
        alert('Please select a conversation.');
        return;
    }

    fetch('/handoff', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            conversation_id: currentConversationId,
        }),
    })
        .then(response => response.json())
        .then(data => {
            if (data.message) {
                alert(data.message);
                fetchConversations();
                document.getElementById('handoff-button').style.display = 'none';
            } else {
                alert('Error assigning chat: ' + data.error);
            }
        })
        .catch(error => console.error('Error during handoff:', error));
});

// Socket.IO event listeners
socket.on('new_message', (data) => {
    console.log('New message received:', data);
    if (data.convo_id === currentConversationId) {
        const chatWindow = document.getElementById('chat-window');
        const div = document.createElement('div');
        div.className = data.sender === 'user' ? 'user-message' : 'ai-message';
        div.textContent = `${data.sender}: ${data.message}`;
        chatWindow.appendChild(div);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }
    fetchConversations(); // Refresh the conversation list
});

socket.on('error', (data) => {
    console.error('Error received:', data);
    alert(data.message);
});

socket.on('handoff', (data) => {
    console.log('Handoff event received:', data);
    const conversationId = data.conversation_id;
    const agent = data.agent;
    const user = data.user;
    const channel = data.channel;

    // Show notification
    alert(`Conversation ${conversationId} from ${user} (${channel}) needs attention.`);

    // Check visibility of the conversation
    pollVisibility(conversationId);

    // Refresh the conversation list immediately
    fetchConversations();
});

// Poll visibility of a conversation
function pollVisibility(conversationId) {
    fetch(`/check-visibility?conversation_id=${conversationId}`)
        .then(response => response.json())
        .then(data => {
            if (data.visible) {
                console.log(`Conversation ${conversationId} is now visible`);
                fetchConversations(); // Refresh the list again to be sure
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
